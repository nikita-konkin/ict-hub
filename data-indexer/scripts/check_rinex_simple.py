import sys
sys.path.insert(0, '/app')
print('Testing updated RINEX indexing...')
from data_indexer import list_rinex_server_structure
print('Function imported successfully')
print('Starting indexing...')
result = list_rinex_server_structure('/mnt/rinex-server')
print(f'Indexing completed. Found {len(result)} years')
for year in result:
    print(f'Year: {year["year"]}, Days: {len(year["days"])}')
    if year['days']:
        sample_days = year['days'][:3]
        print(f'  Sample days: {sample_days}')