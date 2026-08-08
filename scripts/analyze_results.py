#!/usr/bin/env python3
import pandas as pd
import numpy as np

def analyze():
    dataset_path = '/Users/hanafi/Desktop/Rumah/Playground/shopee-scraper/out/ai_training_dataset.csv'
    df = pd.read_csv(dataset_path)
    
    print("="*60)
    print("            DATASET QUALITY ANALYSIS REPORT            ")
    print("="*60)
    print(f"Total Rows: {len(df)}")
    print(f"Total Unique Products (Questions): {df['product_url'].nunique()}")
    print(f"Total Labels:")
    print(df['label'].value_counts())
    print("-"*60)
    
    # 1. Question metrics
    questions = df.drop_duplicates(subset=['product_url'])['Question'].astype(str)
    q_lengths = questions.str.len()
    print("Question Length Statistics (characters):")
    print(f"  Mean:   {q_lengths.mean():.1f}")
    print(f"  Min:    {q_lengths.min()}")
    print(f"  Max:    {q_lengths.max()}")
    print(f"  Median: {q_lengths.median()}")
    print("-"*60)
    
    # 2. Labels per product statistics
    labels_per_product = df.groupby('product_url')['label'].sum()
    print("Positive Labels (1) Per Product Statistics:")
    print(f"  Mean:   {labels_per_product.mean():.2f}")
    print(f"  Min:    {labels_per_product.min()}")
    print(f"  Max:    {labels_per_product.max()}")
    print(f"  Median: {labels_per_product.median()}")
    print(f"  Products with exactly 1 positive label:  {sum(labels_per_product == 1)} ({sum(labels_per_product == 1)/len(labels_per_product)*100:.1f}%)")
    print(f"  Products with multiple positive labels:  {sum(labels_per_product > 1)} ({sum(labels_per_product > 1)/len(labels_per_product)*100:.1f}%)")
    print(f"  Products with zero positive labels:      {sum(labels_per_product == 0)} ({sum(labels_per_product == 0)/len(labels_per_product)*100:.1f}%)")
    print("="*60)
    
    # 3. Print 3 detailed product samples
    print("\n" + "="*60)
    print("                    DETAILED SAMPLES                   ")
    print("="*60)
    
    unique_products = df['product_url'].unique()
    # Pick 3 products with different positive label counts (if possible)
    sample_urls = []
    # Find one with exactly 1 positive label
    p1 = labels_per_product[labels_per_product == 1].index
    if len(p1) > 0:
        sample_urls.append(p1[0])
    # Find one with multiple positive labels
    pm = labels_per_product[labels_per_product > 1].index
    if len(pm) > 0:
        sample_urls.append(pm[0])
    # Fallback to random if needed
    for u in unique_products:
        if u not in sample_urls and len(sample_urls) < 3:
            sample_urls.append(u)
            
    for idx, url in enumerate(sample_urls):
        group = df[df['product_url'] == url].sort_values(by='ID').reset_index(drop=True)
        prod_name = group['product_name'].iloc[0]
        question = group['Question'].iloc[0]
        
        print(f"\nSample {idx+1}: Product: '{prod_name[:70]}...'")
        print(f"Question: \"{question}\"")
        print("-"*60)
        
        # Print all answers with their labels
        for r_idx, row in group.iterrows():
            lbl_str = "✅ [CORRECT (1)]" if row['label'] == 1 else "❌ [INCORRECT (0)]"
            ans_snippet = row['Answer'].replace('\n', ' ')
            if len(ans_snippet) > 120:
                ans_snippet = ans_snippet[:120] + "..."
            print(f"  - Answer {r_idx+1} {lbl_str}:")
            print(f"    \"{ans_snippet}\"")
        print("="*60)

if __name__ == '__main__':
    analyze()
