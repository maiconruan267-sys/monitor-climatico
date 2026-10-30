"""Update the original dashboard schema, preserving every stored date."""
import csv
import json
import math
import os
import requests
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
FIELDS = dict(min='temperature_2m_min', max='temperature_2m_max',
    apparent_min='apparent_temperature_min', apparent_max='apparent_temperature_max',
    weather_code='weather_code', rain='precipitation_sum',
    probability='precipitation_probability_max', precipitation_hours='precipitation_hours',
    wind_speed_max='wind_speed_10m_max', wind_gusts_max='wind_gusts_10m_max',
    wind_direction_dominant='wind_direction_10m_dominant')

def read_payload(path):
    raw = path.read_text(encoding='utf-8-sig').strip()
    prefix = 'window.DADOS_CLIMA = '
    if not raw.startswith(prefix):
        raise ValueError('Formato da base anterior inválido; atualização interrompida.')
    return json.loads(raw[len(prefix):].removesuffix(';'))

def merge_city(old, new):
    records = {}
    for city in (old, new):
        daily = city.get('daily', {})
        for i, day in enumerate(daily.get('time', [])):
            records[day] = {k: daily.get(k, [])[i] if i < len(daily.get(k, [])) else None for k in FIELDS}
    dates = sorted(records)
    return {**new, 'daily': {'time': dates, **{k: [records[d][k] for d in dates] for k in FIELDS}}}

def key(city):
    return city['uf'].strip().upper(), city['cidade'].strip().upper()

def is_current(city, location, today, payload):
    """Reuse only today's complete data for the same registered coordinates."""
    if not city:
        return False
    stamp = city.get('atualizado_em')
    # Legacy bases had only a global timestamp. Trust it only for a full run.
    if 'atualizado_em' not in city and payload.get('status') == 'completo':
        stamp = payload.get('atualizado_em')
    try:
        if datetime.fromisoformat(stamp).astimezone(ZoneInfo('America/Sao_Paulo')).date().isoformat() != today:
            return False
        for field, column in (('latitude', 'Latitude'), ('longitude', 'Longitude')):
            if not math.isclose(float(city[field]), float(location[column].replace(',', '.')), rel_tol=0, abs_tol=1e-7):
                return False
        daily = city['daily']
        dates = daily['time']
        if len(dates) != len(set(dates)):
            return False
        expected = [(date.fromisoformat(today) + timedelta(days=i)).isoformat() for i in range(-15, 16)]
        indexes = [dates.index(day) for day in expected]
        for field in FIELDS:
            values = daily[field]
            if len(values) != len(dates):
                return False
            if field in ('min', 'max', 'rain', 'weather_code'):
                if any(not isinstance(values[i], (int, float)) or not math.isfinite(values[i]) for i in indexes):
                    return False
        return True
    except (TypeError, ValueError, KeyError, IndexError):
        return False

def select_pending(locations, payload, today):
    cities = {key(c): c for c in payload['cidades']}
    pending, reused = [], []
    for location in locations:
        city = cities.get((location['UF'].strip().upper(), location['Cidade'].strip().upper()))
        if is_current(city, location, today, payload):
            reused.append(city)
        else:
            pending.append(location)
    return pending, reused

class IncompleteDataError(ValueError):
    """Valid HTTP response whose weather data is not ready yet."""
    def __init__(self, message, missing=None):
        super().__init__(message)
        self.missing = missing or {}

class IncompleteBatchError(IncompleteDataError):
    def __init__(self, found, errors):
        super().__init__('Dados incompletos no lote; nova tentativa imediata dispensada.')
        self.found, self.errors = found, errors

def validate_daily(daily, today, allow_missing_last_day=False):
    if not isinstance(daily, dict):
        raise IncompleteDataError('Resposta sem bloco daily válido.')
    dates = daily.get('time', [])
    expected = [(date.fromisoformat(today) + timedelta(days=i)).isoformat() for i in range(-15, 16)]
    if dates != expected:
        raise IncompleteDataError('Janela incompleta: esperado D-15 a D+15.',
                                  {'time': [d for d in expected if d not in (dates or [])]})
    for field in FIELDS.values():
        if not isinstance(daily.get(field), list) or len(daily[field]) != len(dates):
            raise IncompleteDataError('Série incompleta: ' + field)
    missing = {}
    for field in ('temperature_2m_min', 'temperature_2m_max', 'precipitation_sum', 'weather_code'):
        invalid = [dates[i] for i, v in enumerate(daily[field])
                   if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)]
        if invalid:
            missing[field] = invalid
    if missing and allow_missing_last_day and all(days == [expected[-1]] for days in missing.values()):
        return missing
    if missing:
        detail = '; '.join(f"{field}: {', '.join(days)}" for field, days in missing.items())
        raise IncompleteDataError('Valores essenciais ausentes/inválidos — ' + detail, missing)

BATCH_SIZE = 7
REQUEST_INTERVAL = 10.0
COLLECTION_BUDGET = 70 * 60
_next_request = 0.0
_session = None

def get_session():
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({'User-Agent': 'Monitor-Climatico-Sul/11.0'})
        # All retries are explicit below, so they obey the global rate limiter.
        _session.mount('https://', requests.adapters.HTTPAdapter(max_retries=0))
    return _session

def close_session():
    global _session
    if _session is not None:
        _session.close()
        _session = None


class RateLimitError(RuntimeError):
    pass

def wait_for_request():
    global _next_request
    time.sleep(max(0, _next_request - time.monotonic()))
    _next_request = time.monotonic() + REQUEST_INTERVAL

def fetch_batch(locations, today):
    query = urlencode(dict(latitude=','.join(r['Latitude'].replace(',', '.') for r in locations),
        longitude=','.join(r['Longitude'].replace(',', '.') for r in locations),
        daily=','.join(FIELDS.values()), timezone='America/Sao_Paulo',
        wind_speed_unit='kmh', past_days=15, forecast_days=16))
    url = 'https://api.open-meteo.com/v1/forecast?' + query
    for attempt in range(3):
        wait_for_request()
        request_started = time.monotonic()
        try:
            with get_session().get(url, timeout=(6.1, 30)) as response:
                response.raise_for_status()
                result = response.json()
            print(f'Resposta recebida em {time.monotonic() - request_started:.1f}s; '
                  f'{len(locations)} cidades; tentativa {attempt + 1}/3', flush=True)
            if not isinstance(result, list):
                result = [result]
            if len(result) != len(locations):
                raise IncompleteBatchError([], [dict(cidade=r['Cidade'], uf=r['UF'],
                    tipo='dados_incompletos', erro='Quantidade de cidades incompleta no lote.') for r in locations])
            output, errors = [], []
            for local, item in zip(locations, result):
                try:
                    missing = validate_daily(item.get('daily') if isinstance(item, dict) else None, today,
                                             allow_missing_last_day=True) or {}
                    if missing:
                        print(f"Último dia pendente: {local['Cidade']}/{local['UF']}; consultar novamente na próxima atualização.", flush=True)
                        # JSON and the dashboard must see null, never NaN or a fabricated zero.
                        for field in missing:
                            item['daily'][field][-1] = None
                except IncompleteDataError as exc:
                    error = dict(cidade=local['Cidade'], uf=local['UF'], tipo='dados_incompletos',
                                 erro=str(exc), campos_datas=exc.missing)
                    errors.append(error)
                    print(f"Dados pendentes: {local['Cidade']}/{local['UF']}: {exc}", flush=True)
                    continue
                output.append(dict(cidade=local['Cidade'], uf=local['UF'],
                    latitude=float(local['Latitude'].replace(',', '.')),
                    longitude=float(local['Longitude'].replace(',', '.')),
                    timezone='America/Sao_Paulo', pendencias=missing,
                    daily={'time': item['daily']['time'], **{k: item['daily'][v] for k,v in FIELDS.items()}}))
            if errors:
                raise IncompleteBatchError(output, errors)
            return output
        except IncompleteDataError:
            raise  # Missing data does not improve by splitting or immediately retrying.
        except requests.HTTPError as exc:
            code = exc.response.status_code
            print(f'HTTP {code}; lote de {len(locations)} cidades; tentativa {attempt + 1}/3', flush=True)
            if code == 429:
                if attempt == 2:
                    raise RateLimitError('Limite da API persistente; base anterior preservada.') from exc
                header = exc.response.headers.get('Retry-After', '')
                delay = max(60 * (attempt + 1), float(header) if header.isdigit() else 0)
                print(f'API limitou consultas; aguardando {delay:.0f}s.', flush=True)
                time.sleep(delay)
            elif attempt == 2:
                raise
            else:
                time.sleep(10 * (attempt + 1))
        except (requests.ConnectionError, requests.Timeout) as exc:
            close_session()
            print(f'Conexão falhou após {time.monotonic() - request_started:.1f}s: '
                  f'{type(exc).__name__}: {exc}; tentativa {attempt + 1}/3', flush=True)
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
        except Exception as exc:
            print(f'{type(exc).__name__}: {exc}; tentativa {attempt + 1}/3', flush=True)
            if attempt == 2:
                raise
            time.sleep(10 * (attempt + 1))

def fetch_adaptive(locations, today):
    try:
        return fetch_batch(locations, today), []
    except IncompleteBatchError as exc:
        return exc.found, exc.errors
    except RateLimitError:
        raise  # Never split a throttled batch into even more requests.
    except (requests.RequestException, TimeoutError, OSError) as exc:
        return [], [dict(cidade=r['Cidade'], uf=r['UF'], erro=str(exc)) for r in locations]
    except Exception as exc:
        if len(locations) == 1:
            row = locations[0]
            return [], [dict(cidade=row['Cidade'], uf=row['UF'], erro=str(exc))]
        mid = len(locations) // 2
        left, errors_left = fetch_adaptive(locations[:mid], today)
        right, errors_right = fetch_adaptive(locations[mid:], today)
        return left + right, errors_left + errors_right

def save_atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(content, encoding='utf-8')
    temp.replace(path)

def main(force_refresh=False):
    target = ROOT / '_sistema/dados_clima.js'
    old = read_payload(target)  # Fail closed: never silently discard history.
    now = datetime.now(ZoneInfo('America/Sao_Paulo'))
    today = now.date().isoformat()
    with (ROOT / '_sistema/Locais.csv').open(encoding='utf-8-sig', newline='') as f:
        locations = list(csv.DictReader(f, delimiter=';'))
    if not locations:
        raise ValueError('Lista de cidades vazia.')
    checkpoint = ROOT / 'historico/coleta-pendente.json'
    cities = {key(c): c for c in old['cidades']}
    for city in cities.values():
        if 'atualizado_em' not in city:
            city['atualizado_em'] = old.get('atualizado_em') if old.get('status') == 'completo' else None
    resumed = []
    if checkpoint.exists() and not force_refresh:
        cached = json.loads(checkpoint.read_text(encoding='utf-8'))
        _, resumed = select_pending(locations, cached, today)
        for city in resumed:
            cities[key(city)] = merge_city(cities.get(key(city), {}), city)
        print(f'Retomada: {len(resumed)} cidades recuperadas do progresso salvo.', flush=True)
    old = dict(old, cidades=list(cities.values()))
    pending, reused = select_pending(locations, old, today)
    if force_refresh:
        pending, reused = locations, []
        print('Teste de coleta completa: consultar todas as cidades novamente.', flush=True)
    print(f'Cadastro: {len(locations)} cidades; {len(reused)} já atualizadas hoje; '
          f'{len(pending)} precisam de coleta.', flush=True)
    if not pending and not resumed:
        print('Base atualizada: nenhuma consulta à API necessária.', flush=True)
        return
    new, errors = [], []
    progress = {key(c): c for c in resumed}
    unavailable_batches = 0
    started = time.monotonic()
    batches = math.ceil(len(pending) / BATCH_SIZE)
    for offset in range(0, len(pending), BATCH_SIZE):
        batch = pending[offset:offset + BATCH_SIZE]
        if time.monotonic() - started >= COLLECTION_BUDGET:
            raise RuntimeError('Tempo de coleta esgotado; progresso salvo para retomada.')
        print(f'Iniciando lote {offset // BATCH_SIZE + 1}/{batches}', flush=True)
        found, failed = fetch_adaptive(batch, today)
        for city in found:
            city['atualizado_em'] = now.isoformat(timespec='seconds')
            progress[key(city)] = city
        save_atomic(checkpoint, json.dumps(dict(cidades=list(progress.values()), status='parcial',
                                                   atualizado_em=now.isoformat(), erros=errors + failed),
                                          ensure_ascii=False, allow_nan=False))
        new.extend(found)
        errors.extend(failed)
        unavailable_batches = unavailable_batches + 1 if (not found and failed and
            all(e.get('tipo') == 'dados_incompletos' for e in failed)) else 0
        if unavailable_batches >= 3:
            raise RuntimeError('API retornou dados incompletos em 3 cargas consecutivas. '
                               'Coleta interrompida sem repetir consultas; diagnóstico e progresso salvos. '
                               'Tente novamente mais tarde, quando a fonte disponibilizar os dados.')
        elapsed = time.monotonic() - started
        done = min(offset + BATCH_SIZE, len(pending))
        print(f'Lote {offset // BATCH_SIZE + 1}/{batches}: {done}/{len(pending)} cidades pendentes; '
              f'{len(errors)} falhas; {elapsed:.1f}s decorridos', flush=True)
    if len(new) + len(reused) < math.ceil(len(locations) * .95):
        raise RuntimeError('Menos de 95% das cidades atualizadas. Progresso salvo; painel anterior preservado.')
    cities = {key(c): c for c in old['cidades']}
    # Freeze legacy provenance before advancing the global update timestamp.
    for city in cities.values():
        if 'atualizado_em' not in city:
            city['atualizado_em'] = old.get('atualizado_em') if old.get('status') == 'completo' else None
    for city in new:
        city['atualizado_em'] = now.isoformat(timespec='seconds')
        cities[key(city)] = merge_city(cities.get(key(city), {}), city)
    dates = sorted({day for c in cities.values() for day in c['daily']['time']})
    pending_days = [dict(cidade=c['cidade'], uf=c['uf'], campos_datas=c['pendencias'])
                    for c in cities.values() if c.get('pendencias')]
    payload = dict(old, pendencias_previsao=pending_days, duracao_coleta_segundos=round(time.monotonic() - started, 1), tamanho_lote=BATCH_SIZE, status='parcial' if errors or pending_days else 'completo',
        atualizado_em=now.isoformat(timespec='seconds'), hoje=today,
        total_esperado=len(locations), total_retornado=len(new) + len(reused),
        total_consultado=len(pending), total_atualizado=len(new), total_reaproveitado=len(reused),
        percentual=round((len(new) + len(reused))/len(locations)*100, 1), erros=errors,
        cidades=list(cities.values()), periodo_armazenado_inicio=dates[0],
        periodo_armazenado_fim=dates[-1], datas_armazenadas=len(dates))
    content = json.dumps(payload, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    # Daily snapshot also preserves the forecast as it was issued on that date.
    save_atomic(ROOT / 'historico' / (today + '.json'), content)
    save_atomic(target, 'window.DADOS_CLIMA = ' + content + ';\n')
    checkpoint.unlink(missing_ok=True)
    print(f'Base salva: {len(cities)} cidades, {len(dates)} datas.')

if __name__ == '__main__':
    try:
        main(force_refresh=os.environ.get('FORCE_REFRESH', '').lower() == 'true')
    finally:
        close_session()

