import yaml

with open('games.yaml', 'r', encoding='utf-8') as f:
    data = yaml.safe_load(f)

all_chars = set()
for title in data.keys():
    for char in title:
        all_chars.add(char)

sorted_chars = sorted(list(all_chars))
print(f"Unique characters: {''.join(sorted_chars)}")
for char in sorted_chars:
    print(f"U+{ord(char):04X} : {char}")
