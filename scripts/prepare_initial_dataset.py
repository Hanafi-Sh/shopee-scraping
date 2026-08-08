#!/usr/bin/env python3
import os
import pandas as pd
import numpy as np
import random

# Set random seed for reproducibility
random.seed(42)
np.random.seed(42)

def prepare_dataset():
    input_path = '/Users/hanafi/Desktop/Rumah/Playground/shopee-scraper-2/out/batch-scraped-reviews.csv'
    output_path = '/Users/hanafi/Desktop/Rumah/Playground/shopee-scraper/out/initial_dataset.csv'

    print(f"Loading scraped reviews from: {input_path}")
    df = pd.read_csv(input_path)
    print(f"Loaded {len(df)} rows.")

    # 1. Clean and filter reviews
    # Drop rows with null comments or comments that are just whitespace
    df = df.dropna(subset=['review_comment'])
    df['review_comment'] = df['review_comment'].astype(str).str.strip()
    df = df[df['review_comment'] != '']

    # Filter out extremely short comments (e.g. less than 10 chars) if possible,
    # but keep products even if they only have short comments by using a fallback
    df['comment_len'] = df['review_comment'].str.len()
    
    print(f"Filtered to {len(df)} reviews with non-empty comments.")

    # 2. Group by product_url and select up to 10 reviews per product
    # We sort reviews within each product by length descending so that:
    # - The longest (most detailed) reviews are selected first
    # - The very longest one (index 0) will be our target review
    df_sorted = df.sort_values(by=['product_url', 'comment_len'], ascending=[True, False])
    
    # Select up to 10 reviews per product
    grouped = df_sorted.groupby('product_url')
    
    final_rows = []
    product_urls = list(grouped.groups.keys())
    
    # 3. Train/Eval Split by product (80% train, 20% eval)
    random.shuffle(product_urls)
    split_idx = int(len(product_urls) * 0.8)
    train_products = set(product_urls[:split_idx])
    
    print(f"Splitting {len(product_urls)} products into {len(train_products)} train and {len(product_urls) - len(train_products)} eval products.")

    for prod_idx, url in enumerate(product_urls):
        group = grouped.get_group(url)
        # Take top 10 reviews
        product_reviews = group.head(10).copy()
        
        # Determine train or eval
        is_train = 1 if url in train_products else 0
        
        # Within these selected reviews:
        # Since we sorted by comment_len descending, the first review (index 0) is the longest
        # We designate it as the target (label = 1) and others as distractors (label = 0)
        product_reviews = product_reviews.reset_index(drop=True)
        
        for rev_idx, row in product_reviews.iterrows():
            # Label 1 for the longest review (first index), 0 for others
            label = 1 if rev_idx == 0 else 0
            
            # Generate unique ID
            unique_id = f"prod_{prod_idx + 1:04d}_rev_{rev_idx + 1:02d}"
            
            final_rows.append({
                'ID': unique_id,
                'product_url': row['product_url'],
                'product_name': row['product_name'],
                'Answer': row['review_comment'],
                'Question': np.nan,  # Initialized as NaN as requested
                'label': label,
                'is_train': is_train
            })

    output_df = pd.DataFrame(final_rows)
    
    # Sort by ID to keep it structured
    output_df = output_df.sort_values(by='ID').reset_index(drop=True)
    
    # Reorder columns as requested: ID, Answer, Question, label, is_train
    # (We also keep product_url and product_name for mapping/debugging but we can place them logically)
    columns_to_save = ['ID', 'Answer', 'Question', 'label', 'is_train', 'product_name', 'product_url']
    output_df = output_df[columns_to_save]
    
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    output_df.to_csv(output_path, index=False)
    
    print(f"Initial dataset successfully created at: {output_path}")
    print(f"Total rows in output: {len(output_df)}")
    print(f"Label distribution:\n{output_df['label'].value_counts()}")
    print(f"Train/Eval distribution:\n{output_df['is_train'].value_counts()}")

if __name__ == '__main__':
    prepare_dataset()
