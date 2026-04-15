import sys
sys.path.insert(0, '/app')
from data_indexer import list_rinex_server_structure
result = list_rinex_server_structure('/mnt/rinex-server')
print(f'Found {len(result)} years')
for year in result:
    print(f'Year: {year["year"]}, Days: {len(year["days"])}')
    if year['days']:
        sorted_days = sorted(year['days'], key=lambda d: d['stations'], reverse=True)
        print(f'  Top days: {sorted_days[:2]}')