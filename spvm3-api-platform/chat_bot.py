import os
# Ensure it uses PyTorch
os.environ["USE_TF"] = "0"
os.environ["USE_TORCH"] = "1"

# pyrefly: ignore [missing-import]
import torch
# pyrefly: ignore [missing-import]
from transformers import AutoModelForCausalLM, AutoTokenizer

def main():
    # The path to your fully trained model
    model_path = r"C:\Users\Sanjay G L\Downloads\spvm3-api-platform\spvm3-api-platform\dataset\150k_conversations_14M_tokens\best_model\best_model_step_28000"
    
    print(f"Loading trained AI model from {model_path}...")
    
    try:
        # Load the tokenizer and the model weights
        tokenizer = AutoTokenizer.from_pretrained(model_path)
        model = AutoModelForCausalLM.from_pretrained(model_path)
    except Exception as e:
        print(f"\nError loading model. Make sure this folder contains both model weights and tokenizer files (like tokenizer.json or vocab.json).")
        print(f"Error details: {e}")
        return

    # Use GPU if available to make generation faster
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(device)
    
    print(f"Model successfully loaded on {device}!\n")
    print("=" * 50)
    print("Welcome to your custom AI Bot!")
    print("Type 'quit' or 'exit' to stop the chat.")
    print("=" * 50)

    # Interactive Chat Loop
    while True:
        try:
            user_input = input("\nYou: ")
        except (KeyboardInterrupt, EOFError):
            break
            
        if user_input.strip().lower() in ['quit', 'exit']:
            print("Goodbye!")
            break
            
        if not user_input.strip():
            continue

        # Format the prompt. This assumes a standard Human/Assistant conversational format. 
        # If your dataset used a different format (like "<user> Hello </user> <bot>"), change this string!
        prompt = f"Human: {user_input}\nAssistant: "
        
        # Tokenize the user's input to feed into the model
        inputs = tokenizer(prompt, return_tensors="pt").to(device)
        
        # Generate the text response
        with torch.no_grad():
            outputs = model.generate(
                **inputs, 
                max_new_tokens=150,     # Max length of the response
                temperature=0.7,        # Higher is more creative/random, lower is more strict
                top_p=0.9,
                do_sample=True,         
                pad_token_id=tokenizer.eos_token_id
            )
            
        # Decode the numbers back into human-readable text
        response = tokenizer.decode(outputs[0], skip_special_tokens=True)
        
        # Strip out the initial prompt so the bot only prints the new answer
        if prompt in response:
            response = response.split(prompt)[-1].strip()
            
        print(f"\nBot: {response}")

if __name__ == "__main__":
    main()
