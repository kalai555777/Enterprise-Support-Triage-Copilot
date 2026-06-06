import pandas as pd
import random
import os

# Set seed for reproducibility
random.seed(42)

# Write alongside this script regardless of the caller's working directory.
OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))

# Templates for each intent
templates = {
    "billing": [
        "My credit card was charged {amount} twice.",
        "I need a refund for my subscription.",
        "How do I update my billing info for company {company_id}?",
        "There is an unexpected charge of {amount} on my invoice.",
        "Cancel my subscription immediately."
    ],
    "bug": [
        "I am getting a {error_code} error on the {feature} page.",
        "The dashboard is completely broken.",
        "Every time I click save, it throws a {error_code} error.",
        "Company {company_id} is experiencing severe latency on {feature}.",
        "Data is not loading on the {feature} widget."
    ],
    "feature": [
        "Please add a {feature} to the platform.",
        "It would be great if we could have a dark mode.",
        "Can you implement an integration with {integration}?",
        "I want to request a new {feature} for our team.",
        "Is there a way to export data to {integration}?"
    ],
    "lockout": [
        "I am locked out of my account.",
        "I cannot reset my password for company {company_id}.",
        "My 2FA is not working.",
        "Please unlock user {user_id}.",
        "I forgot my password and the reset link is expired."
    ]
}

# Fillers for templates
amounts = ["$10", "$49.99", "$100", "$250"]
error_codes = ["500", "404", "403", "timeout"]
features = ["analytics", "reports", "user management", "API"]
integrations = ["Slack", "Salesforce", "Jira", "Teams"]

data = []

# Generate 200 variations per intent
for intent, phrases in templates.items():
    for _ in range(200):
        phrase = random.choice(phrases)
        # Randomly fill templates
        text = phrase.format(
            amount=random.choice(amounts),
            error_code=random.choice(error_codes),
            feature=random.choice(features),
            company_id=random.randint(1000, 9999),
            integration=random.choice(integrations),
            user_id=random.randint(100, 999)
        )
        data.append({"text": text, "label": intent})

# Save to CSV
df = pd.DataFrame(data)
# Shuffle the dataset
df = df.sample(frac=1, random_state=42).reset_index(drop=True)
out_path = os.path.join(OUTPUT_DIR, "tickets.csv")
df.to_csv(out_path, index=False)
print(f"Generated {len(df)} rows in {out_path}")