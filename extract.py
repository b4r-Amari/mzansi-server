with open('server.py', 'r', encoding='utf-8') as file:
    all_lines = file.readlines()
    
# Write lines 4420-4460 to output file
with open('lines_output.txt', 'w', encoding='utf-8') as out:
    for i in range(4419, min(4460, len(all_lines))):
        out.write(f"{i+1}: {all_lines[i]}")
        
print(f"Total lines in file: {len(all_lines)}")
print("Lines written to lines_output.txt")
