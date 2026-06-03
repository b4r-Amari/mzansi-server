with open('server.py', 'r', encoding='utf-8') as f:
    content = f.read()
    
# Find all occurrences of the pattern
import re
pattern = r'schedule\.get\("delivery_days"'
matches = re.finditer(pattern, content)

lines = content.split('\n')
for match in matches:
    pos = match.start()
    line_num = content[:pos].count('\n') + 1
    print(f"Found at line {line_num}")
    # Print context
    for i in range(max(0, line_num-5), min(len(lines), line_num+5)):
        print(f"{i+1}: {lines[i]}")
    print("\n" + "="*50 + "\n")
