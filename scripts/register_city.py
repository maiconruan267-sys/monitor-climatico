"""Validate an owner-submitted registration before changing the shared catalog."""
import csv
import json
import math
import os
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def normalize(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', value.strip().upper()) if not unicodedata.combining(c))

def validate(value, existing):
    city = ' '.join(str(value.get('cidade', '')).split())
    uf = str(value.get('uf', '')).strip().upper()
    if not 2 <= len(city) <= 100 or not all(c.isalpha() or c in " '-." for c in city):
        raise ValueError('Nome de cidade inválido.')
    if uf not in ('RS', 'SC', 'PR'):
        raise ValueError('Selecione RS, SC ou PR.')
    lat = float(str(value.get('latitude', '')).replace(',', '.'))
    lon = float(str(value.get('longitude', '')).replace(',', '.'))
    if not math.isfinite(lat) or not math.isfinite(lon) or not (-34 <= lat <= -22 and -58 <= lon <= -48):
        raise ValueError('Coordenadas fora da região Sul. Confira latitude e longitude.')
    if any(normalize(r['Cidade']) == normalize(city) and r['UF'] == uf for r in existing):
        raise ValueError('Cidade já cadastrada nesta UF.')
    return dict(Cidade=city.upper(), UF=uf, Latitude=f'{lat:.8f}', Longitude=f'{lon:.8f}')

def write_catalog(root=ROOT):
    with (root / '_sistema/Locais.csv').open(encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f, delimiter=';'))
    data = [dict(city=r['Cidade'], st=r['UF'], lat=float(r['Latitude'].replace(',','.')), lon=float(r['Longitude'].replace(',','.'))) for r in rows]
    (root / '_sistema/locais.js').write_text('window.LOCAIS = '+json.dumps(data, ensure_ascii=False)+';\n', encoding='utf-8')

def remove_city(value, rows):
    city = normalize(str(value.get('cidade', '')))
    uf = str(value.get('uf', '')).strip().upper()
    matches = [r for r in rows if normalize(r['Cidade']) == city and r['UF'] == uf]
    if len(matches) != 1:
        raise ValueError('Cidade não encontrada ou cadastro ambíguo. Confira cidade e UF.')
    if len(rows) <= 1:
        raise ValueError('Mantenha pelo menos uma cidade no monitor.')
    return [r for r in rows if r is not matches[0]]

def main():
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    issue = event['issue']
    if issue['user']['login'] != event['repository']['owner']['login']:
        raise ValueError('Somente o proprietário pode cadastrar cidades por este formulário.')
    removal = issue['title'].startswith('[Remoção de cidade]')
    if not removal and not issue['title'].startswith('[Cadastro de cidade]'):
        raise ValueError('Solicitação sem título de cadastro.')
    matches = re.findall(r'```json\s*(.*?)\s*```', issue.get('body') or '', re.S)
    if len(matches) != 1:
        raise ValueError('A solicitação deve conter um bloco JSON de cadastro.')
    path = ROOT / '_sistema/Locais.csv'
    with path.open(encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f, delimiter=';'))
    payload = json.loads(matches[0])
    if removal:
        rows = remove_city(payload, rows)
    else:
        row = validate(payload, rows)
        rows.append(row)
    rows.sort(key=lambda r: (normalize(r['Cidade']), r['UF']))
    with path.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['Cidade','UF','Latitude','Longitude'], delimiter=';')
        writer.writeheader()
        writer.writerows(rows)
    write_catalog()
    print('Cidade removida:' if removal else 'Cidade cadastrada:', payload['cidade'], payload['uf'])

if __name__ == '__main__':
    main()

