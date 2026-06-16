"""
Data processing utilities for LLMRouter scripts
"""

import os
import json
import pandas as pd
import numpy as np
import torch
from tqdm import tqdm
from multiprocessing.dummy import Pool as ThreadPool
from typing import List, Dict

from .embeddings import parallel_embedding_task
from .tensor_utils import to_tensor
from .dataframe_utils import clean_df

def process_final_data(df_all):
    """
    Process final data to match process.py format
    
    Args:
        df_all: DataFrame with all data including query_embedding column
        
    Returns:
        tuple: (df_train_indexed, df_test_indexed, embedding_dict)
    """
    print("=== FINAL PROCESSING ===")
    
    # Step 1: Extract unique query embedding
    print("Extracting unique query embeddings...")
    embedding_df = (
        df_all.groupby(['task_name', 'query', 'gt', 'metric'], as_index=False)
              .first()[['task_name', 'query', 'gt', 'metric', 'query_embedding']]
    )
    
    # Step 2: Generate embedding_id and save pt
    print("Generating embedding IDs and saving tensors...")
    embedding_df['embedding_id'] = range(len(embedding_df))
    
    embedding_dict = {}
    for _, row in embedding_df.iterrows():
        embedding_id = int(row['embedding_id'])
        query_embedding = row['query_embedding']
        
        # Handle numpy array embeddings directly
        if isinstance(query_embedding, np.ndarray):
            embedding_dict[embedding_id] = torch.tensor(query_embedding, dtype=torch.float32)
        else:
            # Handle string embeddings (fallback)
            embedding_dict[embedding_id] = to_tensor(query_embedding)
    
    torch.save(embedding_dict, "query_embeddings.pt")
    print(f"✅ Saved {len(embedding_dict)} vectors to query_embeddings.pt")
    
    # Step 3: Merge embedding_id back to main data
    print("Merging embedding IDs...")
    df_all_indexed = df_all.merge(
        embedding_df[['task_name', 'query', 'gt', 'metric', 'embedding_id']],
        on=['task_name', 'query', 'gt', 'metric'],
        how='left'
    )
    
    # Step 4: Clean DataFrame
    print("Cleaning DataFrame...")
    df_all_indexed = clean_df(df_all_indexed)
    
    # Step 5: Split into train/test (80/20 split)
    print("Splitting into train/test sets...")
    total_size = len(df_all_indexed)
    train_size = int(0.8 * total_size)
    
    df_train_indexed = df_all_indexed.iloc[:train_size]
    df_test_indexed = df_all_indexed.iloc[train_size:]
    
    # Step 6: Save as JSONL files
    print("Saving final files...")
    df_train_indexed.to_json("default_routing_train_data.jsonl", orient="records", lines=True, force_ascii=False)
    df_test_indexed.to_json("default_routing_test_data.jsonl", orient="records", lines=True, force_ascii=False)
    
    print(f"✅ Final files saved:")
    print(f"  - default_routing_train_data.jsonl ({len(df_train_indexed)} records)")
    print(f"  - default_routing_test_data.jsonl ({len(df_test_indexed)} records)")
    print(f"  - query_embeddings.pt ({len(embedding_dict)} embeddings)")
    
    return df_train_indexed, df_test_indexed, embedding_dict


def process_unified_embeddings_and_routing(
    df_train: pd.DataFrame,
    df_test: pd.DataFrame,
    query_data_train: List[Dict],
    query_data_test: List[Dict],
    embedding_output_path: str,
    routing_train_output_path: str,
    routing_test_output_path: str
):
    """
    Process unified embeddings for train+test and generate routing data with embedding_id mapping.
    
    This function:
    1. Generates embeddings for all unique queries (train + test together)
    2. Creates unified .pt file with sequential embedding_id (0, 1, 2, ...)
    3. Maps embedding_id to routing data
    4. Saves routing data JSONL files with correct format
    
    Args:
        df_train: DataFrame with train routing data (after API calls and evaluation)
        df_test: DataFrame with test routing data (after API calls and evaluation)
        query_data_train: Original train query data (for embedding generation)
        query_data_test: Original test query data (for embedding generation)
        embedding_output_path: Path to save unified embeddings .pt file
        routing_train_output_path: Path to save train routing data JSONL
        routing_test_output_path: Path to save test routing data JSONL
        
    Returns:
        tuple: (embedding_dict, df_train_final, df_test_final)
    """
    print("=== UNIFIED EMBEDDINGS AND ROUTING PROCESSING ===")
    
    # Step 1: Combine all unique queries from train and test
    print("Collecting unique queries from train and test data...")
    all_queries = []
    query_to_data = {}  # Map (task_name, query, gt, metric) -> query data
    
    for query_item in query_data_train + query_data_test:
        key = (
            query_item['task_name'],
            query_item['query'],
            tuple(query_item['ground_truth']) if isinstance(query_item['ground_truth'], list) else query_item['ground_truth'],
            query_item['metric']
        )

        if key not in query_to_data:
            query_to_data[key] = query_item
            all_queries.append(query_item)
    
    print(f"Found {len(all_queries)} unique queries (train + test)")
    
    # Step 2: Generate embeddings for all unique queries
    print("Generating embeddings for all unique queries...")
    embedding_results = generate_embeddings_for_data(all_queries, "Generating unified embeddings")
    
    # Step 3: Create embedding mapping and .pt file
    print("Creating unified embeddings .pt file...")
    embedding_dict = {}
    embedding_id_map = {}  # Map (task_name, query, gt, metric) -> embedding_id
    
    for idx, (query_item, (_, embedding, success)) in enumerate(zip(all_queries, embedding_results)):
        if not success:
            print(f"Warning: Failed to generate embedding for query {idx}")
            continue
        
        embedding_id = len(embedding_dict)  # Sequential ID starting from 0
        
        key = (
            query_item['task_name'],
            query_item['query'],
            tuple(query_item['ground_truth']) if isinstance(query_item['ground_truth'], list) else query_item['ground_truth'],
            query_item['metric']
        )
        embedding_id_map[key] = embedding_id
        
        # Convert embedding to tensor
        if isinstance(embedding, torch.Tensor):
            embedding_dict[embedding_id] = embedding.float()
        elif isinstance(embedding, np.ndarray):
            embedding_dict[embedding_id] = torch.tensor(embedding, dtype=torch.float32)
        elif isinstance(embedding, (list, tuple)):
            embedding_dict[embedding_id] = torch.tensor(embedding, dtype=torch.float32)
        else:
            # Fallback: try to_tensor for string representations
            embedding_dict[embedding_id] = to_tensor(embedding)
    
    # Save unified embeddings .pt file
    os.makedirs(os.path.dirname(embedding_output_path), exist_ok=True)
    torch.save(embedding_dict, embedding_output_path)
    print(f"✅ Saved {len(embedding_dict)} unified embeddings to {embedding_output_path}")
    
    # Step 4: Map embedding_id to routing data
    print("Mapping embedding_id to routing data...")
    
    def add_embedding_id(df):
        """Add embedding_id column to DataFrame"""
        embedding_ids = []
        for _, row in df.iterrows():
            # Handle both 'gt' and 'ground_truth' field names
            gt_value = row.get('ground_truth') if 'ground_truth' in row else row.get('gt')
            key = (
                row['task_name'],
                row['query'],
                tuple(gt_value) if isinstance(gt_value, list) else gt_value,
                row['metric']
            )
            embedding_id = embedding_id_map.get(key, None)
            embedding_ids.append(embedding_id)
        df['embedding_id'] = embedding_ids
        return df
    
    df_train_with_ids = add_embedding_id(df_train.copy())
    df_test_with_ids = add_embedding_id(df_test.copy())
    
    # Step 5: Clean and format routing data
    print("Cleaning and formatting routing data...")
    df_train_final = clean_df(df_train_with_ids)
    df_test_final = clean_df(df_test_with_ids)
    
    # Step 6: Format and save routing data JSONL files
    print("Saving routing data files...")
    os.makedirs(os.path.dirname(routing_train_output_path), exist_ok=True)
    os.makedirs(os.path.dirname(routing_test_output_path), exist_ok=True)
    
    # Format to match sample exactly: convert choices to JSON string, ensure all fields present
    def format_routing_record(row):
        """Format a row to match sample routing data format"""
        record = row.to_dict()
        
        # Convert choices to string format matching sample (Python dict string with single quotes)
        # Handle both string and object formats
        if 'choices' in record and record['choices'] is not None:
            if isinstance(record['choices'], str):
                # Already a string - check if it's Python dict format (single quotes) or JSON (double quotes)
                # Sample uses Python dict format, so convert JSON to Python format if needed
                try:
                    # Try to parse as JSON first
                    parsed = json.loads(record['choices'])
                    # Convert back to Python dict string format (matches sample)
                    record['choices'] = str(parsed)
                except (json.JSONDecodeError, ValueError, TypeError):
                    # Already in Python format or invalid - keep as is
                    pass
            elif isinstance(record['choices'], (dict, list)):
                # Convert dict/list to Python dict string format (matches sample)
                record['choices'] = str(record['choices'])
        
        # Ensure all required fields are present
        required_fields = {
            'user_id': None,
            'fig_id': None
        }
        for field, default in required_fields.items():
            if field not in record or pd.isna(record[field]):
                record[field] = default
        
        # Ensure ground_truth field exists (rename from gt if needed)
        if 'gt' in record and 'ground_truth' not in record:
            record['ground_truth'] = record.pop('gt')
        
        return record
    
    # Format and save train data
    train_records = [format_routing_record(row) for _, row in df_train_final.iterrows()]
    with open(routing_train_output_path, 'w', encoding='utf-8') as f:
        for record in train_records:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
    
    # Format and save test data
    test_records = [format_routing_record(row) for _, row in df_test_final.iterrows()]
    with open(routing_test_output_path, 'w', encoding='utf-8') as f:
        for record in test_records:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
    
    print(f"✅ Final files saved:")
    print(f"  - {routing_train_output_path} ({len(df_train_final)} records)")
    print(f"  - {routing_test_output_path} ({len(df_test_final)} records)")
    print(f"  - {embedding_output_path} ({len(embedding_dict)} embeddings)")
    
    return embedding_dict, df_train_final, df_test_final

def generate_embeddings_for_data(data, desc="Generating embeddings"):
    """
    Generate embeddings for a list of data items
    
    Args:
        data: List of data items with 'query' field
        desc: Description for progress bar
        
    Returns:
        list: List of (id, embedding, success) tuples
    """
    print("Generating embeddings...")
    
    task_args = [(id, row['query']) for id, row in enumerate(data)]
    ret_1 = []
    with ThreadPool(100) as p:
        for r in tqdm(p.imap_unordered(parallel_embedding_task, task_args), 
                     total=len(task_args), desc=desc, ncols=100):
            ret_1.append(r)
    
    ret_1.sort(key=lambda x: x[0], reverse=False)
    fail_count = sum(1 for r in ret_1 if not r[-1])
    print(f"Embedding generation complete: Success: {len(ret_1) - fail_count}, Fail: {fail_count}")
    
    return ret_1
