#!/usr/bin/env python3
import os
import json
import argparse
import requests
import time
import pandas as pd
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed

API_ENDPOINT = "https://ai.hanavy.online/v1/messages"
API_KEY = "mm_cl_1ed02376e1e6471163561dec4a0907d757f6e736e0b4f002"

headers = {
    "Content-Type": "application/json",
    "x-api-key": API_KEY,
    "anthropic-version": "2023-06-01"
}

def clean_json(text):
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()

def call_llm(system_prompt, messages, max_tokens=1000, temperature=0.7):
    """
    Call the Anthropic-compatible API endpoint with retries and exponential backoff.
    """
    payload = {
        "model": "claude-3-5-sonnet-20241022",
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": messages,
        "temperature": temperature
    }
    
    max_retries = 20
    for attempt in range(max_retries):
        try:
            response = requests.post(API_ENDPOINT, json=payload, headers=headers, timeout=60)
            if response.status_code == 200:
                resp_json = response.json()
                # Extract content text
                content = resp_json.get("content", [])
                if content and len(content) > 0:
                    return content[0].get("text", "")
                raise ValueError("Empty content in response")
            elif response.status_code in [429, 503]:
                sleep_time = 30
                print(f"API Cooldown/Replenishing (503/429) detected. Sleeping {sleep_time}s before retry (Attempt {attempt+1}/{max_retries})...")
                time.sleep(sleep_time)
            else:
                backoff = min(2 ** attempt, 30)
                print(f"API Error {response.status_code}: {response.text}. Retrying in {backoff}s...")
                time.sleep(backoff)
        except Exception as e:
            backoff = min(2 ** attempt, 30)
            print(f"Connection error: {e}. Retrying in {backoff}s...")
            time.sleep(backoff)
    
    raise RuntimeError("API failed after maximum retries.")

# --- STAGE 1: Generator ---
SYSTEM_GENERATOR = """You are an AI assistant designed to help build a machine learning dataset. Your task is to generate candidate questions based on Shopee product reviews.
Your goal is to brainstorm 3 distinct candidate questions in Indonesian.

Each question must satisfy these requirements:
- The question must be general, simple, and natural.
- It must focus on a SINGLE attribute, claim, or experience mentioned in the Target Review (e.g., shipping speed, product originality, packaging quality, scent, texture, skin compatibility, efficacy, or price).
- The question must be directly and clearly answered by the Target Review.
- DO NOT list multiple clues or stack conditions to make the question unique (e.g., avoid: "Apakah produk ini original dan dikirim menggunakan double bubble wrap serta dapet bonus?"). Keep it simple (e.g., "Apakah pengirimannya cepat?", "Apakah produknya original?").
- DO NOT use meta-phrasings like "Review mana yang...", "Ulasan mana yang...", "Siapa yang...", "Mengapa pembeli...". The question must be a direct question about the product or purchase experience.
- DO NOT quote the review text verbatim, and do not reference reviewer names, dates, or star ratings.

Output format:
Provide exactly 3 candidate questions. Output them as a JSON array of strings:
[
  "Pertanyaan 1...",
  "Pertanyaan 2...",
  "Pertanyaan 3..."
]
Do not include any other text, markdown blocks, or explanation. Just return the raw JSON array."""

def generate_candidates(product_name, target_review, distractors, messages_history=None):
    """
    Generate 3 candidate questions from the target review and distractors.
    If messages_history is provided, append to it to maintain conversation turn.
    """
    distractor_text = "\n".join([f"- \"{d}\"" for d in distractors])
    
    user_prompt = f"""Product Name: {product_name}

Target Review:
"{target_review}"

Distractor Reviews:
{distractor_text}"""

    if messages_history is None:
        messages = [{"role": "user", "content": user_prompt}]
    else:
        messages = list(messages_history)
        messages.append({"role": "user", "content": user_prompt})
        
    response = call_llm(SYSTEM_GENERATOR, messages, temperature=0.7)
    
    # Try parsing response as JSON array
    try:
        cleaned = clean_json(response)
        candidates = json.loads(cleaned)
        if isinstance(candidates, list) and len(candidates) >= 3:
            return candidates[:3], messages + [{"role": "assistant", "content": response}]
    except Exception as e:
        print(f"Failed to parse candidates JSON. Raw response: {response}")
    
    # Fallback parsing line by line
    lines = [l.strip().strip('"').strip('-').strip('0123456789. ') for l in response.split('\n') if l.strip()]
    candidates = [l for l in lines if l]
    if len(candidates) >= 3:
        return candidates[:3], messages + [{"role": "assistant", "content": response}]
        
    return ["Pertanyaan 1", "Pertanyaan 2", "Pertanyaan 3"], messages + [{"role": "assistant", "content": response}]

# --- STAGE 2: Judge / Validator ---
SYSTEM_JUDGE = """You are an expert dataset validator and quality controller. Your task is to select and refine the best question from a list of candidates.
You will be given:
1. Product Name.
2. The "Target Review" (correct answer).
3. 3 candidate questions.

For each candidate question, verify:
1. Is it simple, general, and natural? (No convoluted conditions or multiple stacked clues).
2. Is it directly and clearly answered by the Target Review?
3. Does it avoid meta-phrasings like "Review mana yang..." or "Ulasan mana yang..."?
4. Is it written in natural, fluent Indonesian?

Your task:
- Choose the best question among the 3 candidates.
- Refine/rewrite the chosen question to make it simple, direct, and natural if necessary.
- Output your evaluation and the final question in the following JSON format:
{{
  "valid": true,
  "selected_index": 0/1/2,
  "reason": "Brief explanation of why this question was selected and how it was polished to be simple and natural",
  "final_question": "The final polished simple question in Indonesian"
}}

Do not include any other text or explanation outside this JSON object."""

def evaluate_candidates(product_name, target_review, distractors, candidates, is_final_turn=False):
    distractor_text = "\n".join([f"- \"{d}\"" for d in distractors])
    
    user_prompt = f"""Product Name: {product_name}

Target Review:
"{target_review}"

Distractor Reviews:
{distractor_text}

Candidate Questions:
0: "{candidates[0]}"
1: "{candidates[1]}"
2: "{candidates[2]}"
"""
    if is_final_turn:
        user_prompt += "\n\nThis is the final iteration. You MUST select the best candidate question and polish/rewrite it to make it perfectly valid. Do not mark \"valid\" as false. Return the refined question under \"final_question\" and set \"valid\" to true."

    messages = [{"role": "user", "content": user_prompt}]
    response = call_llm(SYSTEM_JUDGE, messages, temperature=0.2)
    
    try:
        cleaned = clean_json(response)
        eval_json = json.loads(cleaned)
        return eval_json
    except Exception as e:
        print(f"Failed to parse Judge JSON. Raw response: {response}")
        # Return fallback response
        return {
            "valid": True if is_final_turn else False,
            "selected_index": 0,
            "reason": "Fallback due to JSON parse failure",
            "final_question": candidates[0]
        }

# --- STAGE 3: Re-labeler ---
SYSTEM_RELABEL = """You are an expert dataset quality auditor. Your task is to evaluate a single product review and determine if it accurately answers a specific question.
You will be given:
1. Product Name.
2. A Question in Indonesian.
3. A single product review comment.

Determine if the review text contains enough specific details to correctly answer the given Question.
- Set the label to 1 if the review successfully answers the question.
- Set the label to 0 if the review does not contain enough information, talks about unrelated topics, or contradicts the question.

Be objective. Do not assume or extrapolate details not explicitly written in the review.

Output format:
Output your decision as a JSON object containing a "label" key set to either 1 or 0, along with a short reason:
{
  "label": 1,
  "reason": "Brief explanation of why this review does/does not answer the question."
}

Do not include any other text, markdown formatting, or explanation. Just return the raw JSON object."""

def relabel_single_review(product_name, question, review_comment):
    user_prompt = f"""Product Name: {product_name}
Question: {question}

Review to Evaluate:
"{review_comment}"
"""
    messages = [{"role": "user", "content": user_prompt}]
    try:
        response = call_llm(SYSTEM_RELABEL, messages, temperature=0.1)
        cleaned = clean_json(response)
        res_json = json.loads(cleaned)
        return int(res_json.get("label", 0))
    except Exception as e:
        print(f"Error in relabeling single review: {e}")
        return 0

def process_product(product_name, reviews, target_idx=0):
    """
    Run Generator-Critic loop and Re-labeler for a single product.
    If no positive label is found in Stage 3, we retry up to 3 times to regenerate the question.
    """
    target_review = reviews[target_idx]
    distractors = [r for i, r in enumerate(reviews) if i != target_idx]
    
    print(f"Processing product: '{product_name[:40]}...' with {len(reviews)} reviews.")
    
    max_regen_attempts = 3
    for attempt in range(1, max_regen_attempts + 1):
        if attempt > 1:
            print(f"  Attempt {attempt}/{max_regen_attempts}: No positive label was generated. Discarding previous question and regenerating...")
            
        # Run Stage 1 & 2 Generator-Critic loop up to 3 turns
        messages_history = None
        final_question = None
        
        for turn in range(1, 4):
            print(f"    Turn {turn}/3: Generating candidates...")
            candidates, messages_history = generate_candidates(product_name, target_review, distractors, messages_history)
            
            print(f"    Turn {turn}/3: Evaluating candidates...")
            is_final_turn = (turn == 3)
            evaluation = evaluate_candidates(product_name, target_review, distractors, candidates, is_final_turn)
            
            if evaluation.get("valid") is True:
                final_question = evaluation.get("final_question")
                print(f"    Success on Turn {turn}! Question: {final_question[:50]}...")
                break
            else:
                print(f"    Rejected on Turn {turn}. Reason: {evaluation.get('reason')}")
                # Feed rejection critique back to Generator context
                messages_history.append({
                    "role": "user",
                    "content": f"All of your candidate questions were rejected by the Judge for the following reason:\n{evaluation.get('reason')}\n\nPlease generate 3 NEW candidate questions that completely resolve these issues. Ensure they do not collide with any of the distractor reviews."
                })
                
        if not final_question:
            # Fallback if somehow loop terminates without valid question
            final_question = target_review[:100] + "?"
            print(f"    Fallback Question generated: {final_question}")
            
        # Stage 3: Parallel Re-labeler for all reviews of this product
        print(f"    Stage 3: Running parallel re-labeler for {len(reviews)} reviews...")
        labels = [0] * len(reviews)
        
        with ThreadPoolExecutor(max_workers=min(len(reviews), 10)) as executor:
            futures = {executor.submit(relabel_single_review, product_name, final_question, review): i for i, review in enumerate(reviews)}
            for future in as_completed(futures):
                rev_idx = futures[future]
                try:
                    labels[rev_idx] = future.result()
                except Exception as e:
                    print(f"    Error in thread for review {rev_idx}: {e}")
                    labels[rev_idx] = 1 if rev_idx == target_idx else 0
                    
        print(f"    Stage 3 complete. New labels: {labels}")
        
        # If we have at least one positive label, we succeed!
        if sum(labels) > 0:
            return final_question, labels
            
    # Hard fallback if after max_regen_attempts we still have 0 positive labels:
    # Force the target review to be labeled 1 so there's at least one correct answer
    print(f"  WARNING: No positive label generated after {max_regen_attempts} attempts. Forcing target review (idx {target_idx}) to be correct.")
    labels[target_idx] = 1
    return final_question, labels


def main():
    parser = argparse.ArgumentParser(description="Generate Questions and Dynamic Labels for Shopee Reviews Dataset")
    parser.add_argument("--dry-run", action="store_true", help="Run on only the first 3 products to test")
    args = parser.parse_args()
    
    input_csv = "/Users/hanafi/Desktop/Rumah/Playground/shopee-scraper/out/initial_dataset.csv"
    output_csv = "/Users/hanafi/Desktop/Rumah/Playground/shopee-scraper/out/ai_training_dataset.csv"
    checkpoint_file = "/Users/hanafi/Desktop/Rumah/Playground/shopee-scraper/scripts/checkpoint_dataset.json"
    
    if not os.path.exists(input_csv):
        print(f"Input file not found: {input_csv}. Please run scripts/prepare_initial_dataset.py first.")
        return
        
    df = pd.read_csv(input_csv)
    df['Question'] = df['Question'].astype(object)
    
    # Find unique products
    # We group by product_url to ensure grouping is correct
    grouped = df.groupby('product_url')
    unique_products = list(grouped.groups.keys())
    
    if args.dry_run:
        print("--- RUNNING IN DRY RUN MODE (3 PRODUCTS ONLY) ---")
        unique_products = unique_products[:3]
        
    # Load checkpoint if exists
    checkpoint = {}
    if os.path.exists(checkpoint_file):
        try:
            with open(checkpoint_file, "r") as f:
                checkpoint = json.load(f)
            print(f"Loaded checkpoint with {len(checkpoint)} processed products.")
        except Exception as e:
            print(f"Failed to load checkpoint file: {e}")
            
    import threading
    checkpoint_lock = threading.Lock()
    total_prods = len(unique_products)
    
    def process_and_checkpoint(prod_url, idx):
        with checkpoint_lock:
            if prod_url in checkpoint:
                return
        
        group = df[df['product_url'] == prod_url].copy()
        
        # Find which index has label == 1 originally (this is our target review)
        # Sort so that reviews are in exact order they appear in the CSV
        group = group.sort_values(by='ID').reset_index(drop=True)
        target_indices = group[group['label'] == 1].index.tolist()
        target_idx = target_indices[0] if target_indices else 0
        
        reviews = group['Answer'].tolist()
        product_name = group['product_name'].iloc[0]
        
        try:
            print(f"\n[{idx+1}/{total_prods}] Starting: {prod_url[:80]}...")
            final_question, labels = process_product(product_name, reviews, target_idx)
            
            # Save to checkpoint (thread safe)
            with checkpoint_lock:
                checkpoint[prod_url] = {
                    "question": final_question,
                    "labels": labels
                }
                with open(checkpoint_file, "w") as f:
                    json.dump(checkpoint, f, indent=2)
            print(f"[{idx+1}/{total_prods}] Checkpoint saved.")
        except Exception as e:
            print(f"Error processing product {prod_url}: {e}")
            if args.dry_run:
                raise e

    # Process each product sequentially (1 product at a time to prevent rate limits)
    with ThreadPoolExecutor(max_workers=1) as executor:
        futures = [executor.submit(process_and_checkpoint, prod_url, idx) 
                   for idx, prod_url in enumerate(unique_products)]
        for future in as_completed(futures):
            # This will raise any exceptions that occurred during thread execution
            future.result()
                
    # Update df with questions and labels
    print("\nMerging questions and labels back to dataset...")
    for prod_url, data in checkpoint.items():
        # Get matching rows in df
        mask = df['product_url'] == prod_url
        if not mask.any():
            continue
        
        group_indices = df[mask].index.tolist()
        # Sort indices to match sorted IDs
        group_df = df.loc[group_indices].sort_values(by='ID')
        sorted_indices = group_df.index.tolist()
        
        final_question = data["question"]
        labels = data["labels"]
        
        # Assign questions
        df.loc[sorted_indices, 'Question'] = final_question
        
        # Assign labels (handling potential size mismatches)
        for i, idx in enumerate(sorted_indices):
            if i < len(labels):
                df.loc[idx, 'label'] = labels[i]
                
    # Filter dataset to only keep products that have been processed (important if resuming or dry-run)
    processed_urls = set(checkpoint.keys())
    final_df = df[df['product_url'].isin(processed_urls)].copy()
    
    # Save final dataset
    final_df.to_csv(output_csv, index=False)
    print(f"\nFinal dataset saved to: {output_csv}")
    print(f"Total processed rows: {len(final_df)}")
    print(f"Label distribution:\n{final_df['label'].value_counts()}")
    print(f"Null questions remaining: {final_df['Question'].isnull().sum()}")

if __name__ == "__main__":
    main()
