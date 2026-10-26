"""Update the original dashboard schema, preserving every stored date."""
import csv
import json
import math
import time
from datetime import datetime
from pathlib import Path
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

def fetch_city(local, today):
    query = urlencode(dict(latitude=local['Latitude'].replace(',', '.'),
        longitude=local['Longitude'].replace(',', '.'), daily=','.join(FIELDS.values()),
        timezone='America/Sao_Paulo', wind_speed_unit='kmh', past_days=15, forecast_days=16))
    req = Request('https://api.open-meteo.com/v1/forecast?' + query,
                  headers={'User-Agent': 'Monitor-Climatico-Sul/10.0'})
    for attempt in range(3):
        try:
            with urlopen(req, timeout=60) as response:
                result = json.load(response)
            validate_daily(result['daily'], today)
            return dict(cidade=local['Cidade'], uf=local['UF'],
                latitude=float(local['Latitude'].replace(',', '.')),
                longitude=float(local['Longitude'].replace(',', '.')),
                timezone='America/Sao_Paulo',
                daily={'time': result['daily']['time'], **{k: result['daily'][v] for k,v in FIELDS.items()}})
        except Exception:
            if attempt == 2:
                raise
            time.sleep(10 * (attempt + 1))

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
    for i, local in enumerate(locations, 1):
        try:
            new.append(fetch_city(local, today))
        except Exception as exc:
            errors.append(dict(cidade=local['Cidade'], uf=local['UF'], erro=str(exc)))
        print(f'{i}/{len(locations)} cidades; {len(errors)} falhas', flush=True)
        time.sleep(1.1)
    if len(new) < math.ceil(len(locations) * .95):
        raise RuntimeError('Menos de 95% das cidades atualizadas. Base anterior preservada.')
    cities = {key(c): c for c in old['cidades']}
    for city in new:
        cities[key(city)] = merge_city(cities.get(key(city), {}), city)
    dates = sorted({day for c in cities.values() for day in c['daily']['time']})
    payload = dict(old, status='parcial' if errors else 'completo',
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
