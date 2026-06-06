import os

import pandas as pd
from sklearn.model_selection import train_test_split

# 1. Operate alongside this script regardless of the caller's working directory.
folder_path = os.path.dirname(os.path.abspath(__file__))

# 2. Safety check: Ensure the directory exists before doing anything
os.makedirs(folder_path, exist_ok=True)

# 3. Load the textbook (Make sure tickets.csv was moved into this folder!)
df = pd.read_csv(f"{folder_path}/tickets.csv")

# 4. First split: 70% Train, 30% Temp (Val + Test)
train_df, temp_df = train_test_split(df, test_size=0.30, stratify=df["label"], random_state=42)

# 5. Second split: Split the 30% Temp in half for 15% Val and 15% Test
val_df, test_df = train_test_split(
    temp_df, test_size=0.50, stratify=temp_df["label"], random_state=42
)

# 6. Save the splits
train_df.to_csv(f"{folder_path}/train.csv", index=False)
val_df.to_csv(f"{folder_path}/val.csv", index=False)
test_df.to_csv(f"{folder_path}/test.csv", index=False)

print(f"Train size: {len(train_df)}")
print(f"Val size: {len(val_df)}")
print(f"Test size: {len(test_df)}")
