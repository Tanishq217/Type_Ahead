import os
import csv
import random
import math

def generate_dataset(output_path: str, target_count: int = 105000):
    """
    Generates a realistic search query dataset containing at least 100,000+ entries.
    Assigns search frequencies using a Zipfian power-law distribution to mimic real-world search engines.
    """
    print(f"Generating {target_count} queries to {output_path}...")
    
    # Core search subjects and domains
    subjects = [
        "iphone", "macbook", "ipad", "samsung galaxy", "sony headphones", "dell xps", "playstation", "xbox", "nintendo switch",
        "java", "python", "javascript", "react", "html", "css", "rust", "golang", "typescript", "c++", "c#", "ruby", "php",
        "docker", "kubernetes", "aws", "gcp", "azure", "git", "github", "linux", "sql", "postgresql", "mongodb", "redis",
        "machine learning", "data science", "chatgpt", "artificial intelligence", "deep learning", "neural networks",
        "fastapi", "django", "flask", "spring boot", "nodejs", "express", "nextjs", "vue", "angular", "svelte",
        "how to learn", "best tutorial for", "crash course in", "guide to", "exercises in", "projects for",
        "flight ticket to", "weather in", "hotels in", "restaurants near", "best movie on", "news about"
    ]
    
    modifiers = [
        "tutorial", "for beginners", "course", "documentation", "example", "vs python", "vs javascript", "salary",
        "jobs", "interview questions", "cheat sheet", "features", "best practices", "guide", "roadmap", "projects",
        "pro", "max", "price", "review", "specs", "deals", "alternative", "online", "download", "free", "latest version"
    ]

    general_nouns = [
        "book", "laptop", "phone", "monitor", "keyboard", "mouse", "desk", "chair", "shoes", "shirt", "backpack",
        "watch", "camera", "drone", "speaker", "charger", "cable", "adapter", "software", "game", "app", "framework",
        "tool", "service", "tutorial", "class", "degree", "jobs", "news", "weather", "recipe", "song", "movie", "show"
    ]
    
    general_adjectives = [
        "best", "cheap", "expensive", "top", "free", "premium", "new", "used", "refurbished", "fast", "slow", "easy",
        "hard", "simple", "complex", "modern", "old", "latest", "popular", "trending", "local", "global", "online"
    ]

    unique_queries = set()
    
    # 1. Generate core queries from combinations
    # Populates head and mid-tail queries
    for sub in subjects:
        unique_queries.add(sub)
        for mod in modifiers:
            unique_queries.add(f"{sub} {mod}")
            
    for adj in general_adjectives:
        for noun in general_nouns:
            unique_queries.add(f"{adj} {noun}")
            for sub in ["python", "iphone", "java", "react", "aws", "laptop"]:
                unique_queries.add(f"{adj} {sub} {noun}")

    # 2. Fill the remaining queries up to target_count using random combinations
    while len(unique_queries) < target_count:
        # Generate random combinations of words to represent long-tail searches
        words = []
        if random.random() < 0.4:
            words.append(random.choice(general_adjectives))
        words.append(random.choice(subjects))
        if random.random() < 0.6:
            words.append(random.choice(general_nouns))
        if random.random() < 0.3:
            words.append(random.choice(modifiers))
            
        query = " ".join(words).strip().lower()
        if query and len(query) > 2:
            unique_queries.add(query)

    # Convert to list and shuffle to prevent ordering bias
    query_list = list(unique_queries)[:target_count]
    random.shuffle(query_list)

    # 3. Apply Zipfian power-law distribution for frequency counts
    # This creates a small number of ultra-popular "head" terms and a huge tail of low-frequency terms
    # count = Scale / (rank ^ exponent)
    exponent = 0.82
    scale = 800000  # Highest frequency count
    
    rows = []
    for rank, query in enumerate(query_list, start=1):
        count = int(scale / math.pow(rank, exponent))
        # Ensure count is at least 1
        count = max(1, count)
        rows.append([query, count])

    # Sort by count desc so head queries are listed first
    rows.sort(key=lambda x: x[1], reverse=True)

    # Write to CSV
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["query", "count"])
        writer.writerows(rows)
        
    print(f"Successfully generated {len(rows)} queries. Head query: '{rows[0][0]}' with count {rows[0][1]}.")

if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_csv = os.path.join(script_dir, "queries.csv")
    generate_dataset(output_csv)
