import os
os.environ["USE_TF"] = "0"
os.environ["USE_TORCH"] = "1"
# pyrefly: ignore [missing-import]
import torch
# pyrefly: ignore [missing-import]
from datasets import load_from_disk
# pyrefly: ignore [missing-import]
from transformers import AutoModelForCausalLM, TrainingArguments, Trainer, default_data_collator

def main():
    # 1. Load the pre-tokenized HuggingFace Datasets
    base_dir = r"C:\Users\Sanjay G L\Downloads\spvm3-api-platform\spvm3-api-platform\dataset\150k_conversations_14M_tokens\150k_conversations_14M_tokens"
    train_dir = os.path.join(base_dir, "final_train_dataset")
    val_dir = os.path.join(base_dir, "final_val_dataset")

    print("1. Loading pre-tokenized datasets from disk...")
    train_dataset = load_from_disk(train_dir)
    val_dataset = load_from_disk(val_dir)
    
    print(f"Loaded {len(train_dataset)} training examples and {len(val_dataset)} validation examples.")

    # 2. Add the 'labels' column
    # For Causal Language Modeling (like ChatGPT, Llama, GPT-2), the labels are the same as the input_ids.
    # The model automatically shifts them internally to predict the next token.
    print("2. Preparing labels for causal language modeling...")
    def add_labels(example):
        example['labels'] = example['input_ids'].copy()
        return example
        
    train_dataset = train_dataset.map(add_labels, desc="Mapping train labels")
    val_dataset = val_dataset.map(add_labels, desc="Mapping val labels")

    # 3. Load the Model
    # Important: You must use the same base model here that you used to tokenize the dataset!
    # I am defaulting to "gpt2", but if you tokenized this with "meta-llama/Llama-3.2-1B", change it below.
    model_name = r"C:\Users\Sanjay G L\Downloads\spvm3-api-platform\spvm3-api-platform\dataset\150k_conversations_14M_tokens\best_model\best_model_step_28000"
    print(f"3. Loading base model: {model_name} ...")
    model = AutoModelForCausalLM.from_pretrained(model_name)

    # 4. Set up Training Arguments
    output_dir = r"C:\Users\Sanjay G L\Downloads\spvm3-api-platform\spvm3-api-platform\dataset\150k_conversations_14M_tokens\best_model"
    os.makedirs(output_dir, exist_ok=True)
    
    training_args = TrainingArguments(
        output_dir=output_dir,
        eval_strategy="steps",
        eval_steps=500,
        logging_steps=100,
        save_steps=500,
        per_device_train_batch_size=4,
        per_device_eval_batch_size=4,
        gradient_accumulation_steps=4,
        num_train_epochs=1,  # Kept to 1 for faster testing, increase to 3 for real training
        learning_rate=5e-5,
        fp16=torch.cuda.is_available(), # Use mixed precision if a GPU is available for speed
        save_total_limit=2,
        load_best_model_at_end=True,
    )

    # 5. Initialize the Trainer
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=val_dataset,
        data_collator=default_data_collator,
    )

    # 6. Train the model
    print("4. Starting model training (this may take a while depending on your GPU)...")
    trainer.train()

    # 7. Save the final model
    final_output_path = os.path.join(output_dir, "final")
    print(f"5. Saving the final trained model to {final_output_path}...")
    trainer.save_model(final_output_path)
    print("Training complete! You can now load this model for generation.")

if __name__ == "__main__":
    main()
