import re

def strip_date_prefix(filename: str) -> str:
    # Match YYYYMMDDHHMMSS_ or YYYY-MM-DD_HH-MM-SS_ or similar date prefixes
    # Pattern 1: 14 digits followed by underscore
    # Pattern 2: YYYY-MM-DD_HH-MM-SS_
    # Pattern 3: YYYYMMDD_HHMMSS_
    
    patterns = [
        r'^\d{14}_',
        r'^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_',
        r'^\d{8}_\d{6}_',
        r'^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z?_',
    ]
    
    res = filename
    for pattern in patterns:
        res = re.sub(pattern, '', res)
        if res != filename:
            break
            
    return res

print(strip_date_prefix("20250509123456_Consolelog.txt.0"))
print(strip_date_prefix("2025-05-09_12-34-56_Consolelog.txt.0"))
print(strip_date_prefix("20250509_123456_Consolelog.txt.0"))
print(strip_date_prefix("Consolelog.txt.0"))
