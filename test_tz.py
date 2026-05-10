import re
from datetime import datetime
from zoneinfo import ZoneInfo

def convert_to_cet(match, year_hint=2026):
    tz_utc = ZoneInfo("UTC")
    tz_cet = ZoneInfo("Europe/Berlin") # CET/CEST
    
    ts_str = match.group(0)
    
    try:
        if re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}', ts_str):
            dt = datetime.strptime(ts_str[:19], "%Y-%m-%dT%H:%M:%S")
            dt = dt.replace(tzinfo=tz_utc)
            dt_cet = dt.astimezone(tz_cet)
            return dt_cet.strftime("%Y-%m-%dT%H:%M:%S") + ts_str[19:]
            
        elif re.match(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', ts_str):
            dt = datetime.strptime(ts_str[:19], "%Y-%m-%d %H:%M:%S")
            dt = dt.replace(tzinfo=tz_utc)
            dt_cet = dt.astimezone(tz_cet)
            return dt_cet.strftime("%Y-%m-%d %H:%M:%S") + ts_str[19:]
            
        elif re.match(r'^\d{6}-\d{2}:\d{2}:\d{2}', ts_str):
            dt = datetime.strptime(ts_str[:15], "%y%m%d-%H:%M:%S")
            dt = dt.replace(tzinfo=tz_utc)
            dt_cet = dt.astimezone(tz_cet)
            return dt_cet.strftime("%y%m%d-%H:%M:%S") + ts_str[15:]
            
        elif re.match(r'^[A-Z][a-z]{2}\s+\d+\s+\d{2}:\d{2}:\d{2}', ts_str):
            # Apr  8 10:50:17
            # We need to add a year to handle DST correctly, then remove it
            # Let's use year_hint
            dt_str = f"{year_hint} {ts_str}"
            # %b %d %H:%M:%S
            # Handle variable spaces
            dt_str_normalized = re.sub(r'\s+', ' ', dt_str)
            dt = datetime.strptime(dt_str_normalized, "%Y %b %d %H:%M:%S")
            dt = dt.replace(tzinfo=tz_utc)
            dt_cet = dt.astimezone(tz_cet)
            # Return in original format: %b %e %H:%M:%S (pad day with space if single digit)
            day_str = str(dt_cet.day).rjust(2, ' ')
            return dt_cet.strftime(f"%b {day_str} %H:%M:%S")
            
    except Exception as e:
        print(f"Error parsing {ts_str}: {e}")
        return ts_str
        
    return ts_str

def process_line(line):
    # Regex to match the 4 formats
    pattern = r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}|\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}|\d{6}-\d{2}:\d{2}:\d{2}|[A-Z][a-z]{2}\s+\d+\s+\d{2}:\d{2}:\d{2})'
    
    # We only want to replace timestamps at the beginning of the line or after some specific markers, 
    # but replacing all might be fine if we are careful. Let's just replace all that match.
    return re.sub(pattern, lambda m: convert_to_cet(m), line)

lines = [
    "2026-03-25T17:00:19 telekom: W019-1 WIFI.INFO",
    "******************** LOG_MERGE_MARKER: 2026-04-08 11:01:35 ********************",
    "260409-01:19:19 [ERROR] something",
    "Apr  8 10:50:17 airties: info",
    "Apr 18 10:50:17 airties: info"
]

for l in lines:
    print(f"Orig: {l}")
    print(f"New : {process_line(l)}")
    print("-")

