from data_indexer import list_rinex_server_structure
import time
start = time.time()
try:
    result = list_rinex_server_structure('/mnt/rinex-server')
    end = time.time()
    print(f'Indexing took {end-start:.2f} seconds')
    print(f'Found {len(result)} years')
    for year in result[:2]:
        print(f'Year: {year["year"]}, Days: {len(year["days"])}')
        for day in year['days'][:3]:
            print(f'  Day: {day["day"]}, Stations: {day["stations"]}')
except Exception as e:
    print(f'Error: {e}')
    import traceback
    traceback.print_exc()