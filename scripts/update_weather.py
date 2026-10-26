"""Update the original dashboard schema, preserving every stored date."""
import csv
import json
import math
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
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

def validate_daily(daily, today):
    dates = daily.get('time', [])
    if len(dates) != 31 or dates[15] != today or dates != sorted(set(dates)):
        raise ValueError('Janela incompleta: esperado D-15 a D+15.')
    for field in FIELDS.values():
        if len(daily.get(field, [])) != len(dates):
            raise ValueError('Série incompleta: ' + field)
    for field in ('temperature_2m_min', 'temperature_2m_max', 'precipitation_sum', 'weather_code'):
        if any(v is None for v in daily[field]):
            raise ValueError('Valores essenciais ausentes: ' + field)

BATCH_SIZE = 7
REQUEST_INTERVAL = 10.0
_next_request = 0.0

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
    req = Request('https://api.open-meteo.com/v1/forecast?' + query,
                  headers={'User-Agent': 'Monitor-Climatico-Sul/10.1'})
    for attempt in range(3):
        wait_for_request()
        try:
            with urlopen(req, timeout=90) as response:
                result = json.load(response)
            if not isinstance(result, list):
                result = [result]
            if len(result) != len(locations):
                raise ValueError('Quantidade de cidades incompleta no lote.')
            output = []
            for local, item in zip(locations, result):
                validate_daily(item['daily'], today)
                output.append(dict(cidade=local['Cidade'], uf=local['UF'],
                    latitude=float(local['Latitude'].replace(',', '.')),
                    longitude=float(local['Longitude'].replace(',', '.')),
                    timezone='America/Sao_Paulo',
                    daily={'time': item['daily']['time'], **{k: item['daily'][v] for k,v in FIELDS.items()}}))
            return output
        except HTTPError as exc:
            if exc.code == 429:
                if attempt == 2:
                    raise RateLimitError('Limite da API persistente; base anterior preservada.') from exc
                header = exc.headers.get('Retry-After', '') if exc.headers else ''
                delay = max(60 * (attempt + 1), float(header) if header.isdigit() else 0)
                print(f'API limitou consultas; aguardando {delay:.0f}s.', flush=True)
                time.sleep(delay)
            elif attempt == 2:
                raise
            else:
                time.sleep(10 * (attempt + 1))
        except Exception:
            if attempt == 2:
                raise
            time.sleep(10 * (attempt + 1))

def fetch_adaptive(locations, today):
    try:
        return fetch_batch(locations, today), []
    except RateLimitError:
        raise  # Never split a throttled batch into even more requests.
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

def main():
    target = ROOT / '_sistema/dados_clima.js'
    old = read_payload(target)  # Fail closed: never silently discard history.
    now = datetime.now(ZoneInfo('America/Sao_Paulo'))
    today = now.date().isoformat()
    with (ROOT / '_sistema/Locais.csv').open(encoding='utf-8-sig', newline='') as f:
        locations = list(csv.DictReader(f, delimiter=';'))
    if not locations:
        raise ValueError('Lista de cidades vazia.')
    new, errors = [], []
    started = time.monotonic()
    batches = math.ceil(len(locations) / BATCH_SIZE)
    for offset in range(0, len(locations), BATCH_SIZE):
        batch = locations[offset:offset + BATCH_SIZE]
        found, failed = fetch_adaptive(batch, today)
        new.extend(found)
        errors.extend(failed)
        elapsed = time.monotonic() - started
        done = min(offset + BATCH_SIZE, len(locations))
        print(f'Lote {offset // BATCH_SIZE + 1}/{batches}: {done}/{len(locations)} cidades; '
              f'{len(errors)} falhas; {elapsed:.1f}s decorridos', flush=True)
    if len(new) < math.ceil(len(locations) * .95):
        raise RuntimeError('Menos de 95% das cidades atualizadas. Base anterior preservada.')
    cities = {key(c): c for c in old['cidades']}
    for city in new:
        cities[key(city)] = merge_city(cities.get(key(city), {}), city)
    dates = sorted({day for c in cities.values() for day in c['daily']['time']})
    payload = dict(old, duracao_coleta_segundos=round(time.monotonic() - started, 1), tamanho_lote=BATCH_SIZE, status='parcial' if errors else 'completo',
        atualizado_em=now.isoformat(timespec='seconds'), hoje=today,
        total_esperado=len(locations), total_retornado=len(new),
        percentual=round(len(new)/len(locations)*100, 1), erros=errors,
        cidades=list(cities.values()), periodo_armazenado_inicio=dates[0],
        periodo_armazenado_fim=dates[-1], datas_armazenadas=len(dates))
    content = json.dumps(payload, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    # Daily snapshot also preserves the forecast as it was issued on that date.
    save_atomic(ROOT / 'historico' / (today + '.json'), content)
    save_atomic(target, 'window.DADOS_CLIMA = ' + content + ';\n')
    print(f'Base salva: {len(cities)} cidades, {len(dates)} datas.')

if __name__ == '__main__':
    main()
