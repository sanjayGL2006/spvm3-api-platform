import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, accuracy_score
import pickle
import glob
import os

def main():
    dataset_dir = r"C:\Users\Sanjay G L\Downloads\spvm3-api-platform\spvm3-api-platform\dataset\archive"
    
    print("1. Loading datasets...")
    # Find all CSV files in the directory
    all_files = glob.glob(os.path.join(dataset_dir, "*.csv"))
    
    if not all_files:
        print(f"No CSV files found in {dataset_dir}")
        return

    df_list = []
    for filename in all_files:
        print(f"Loading {os.path.basename(filename)}...")
        df = pd.read_csv(filename)
        # Ensure we only keep the text and label columns
        if 'text' in df.columns and 'label' in df.columns:
            df_list.append(df[['text', 'label']])
        else:
            print(f"Warning: {filename} missing 'text' or 'label' column. Skipping.")

    # Combine all loaded CSVs into a single DataFrame
    df = pd.concat(df_list, axis=0, ignore_index=True)
    
    # Drop rows with missing text or labels
    df = df.dropna(subset=['text', 'label'])
    
    print(f"Total dataset size: {len(df)} samples")
    
    X = df['text']
    y = df['label']

    print("\n2. Splitting into Train and Test sets...")
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    print("\n3. Vectorizing text (TF-IDF)...")
    # We use TF-IDF to convert the text into numerical vectors. 
    # ngram_range=(1,2) includes both single words and pairs of words.
    vectorizer = TfidfVectorizer(max_features=50000, ngram_range=(1, 2), stop_words='english')
    X_train_vec = vectorizer.fit_transform(X_train)
    X_test_vec = vectorizer.transform(X_test)

    print("\n4. Training Logistic Regression model...")
    # Using balanced class weight in case the dataset has imbalanced labels
    model = LogisticRegression(max_iter=1000, class_weight='balanced', n_jobs=-1)
    model.fit(X_train_vec, y_train)

    print("\n5. Evaluating model...")
    y_pred = model.predict(X_test_vec)
    print(f"Accuracy: {accuracy_score(y_test, y_pred):.4f}")
    print("\nClassification Report:")
    print(classification_report(y_test, y_pred))

    print("\n6. Saving model and vectorizer...")
    # Save the trained model and the vectorizer so they can be loaded later for inference
    with open('essay_classifier_model.pkl', 'wb') as f:
        pickle.dump(model, f)
    with open('essay_vectorizer.pkl', 'wb') as f:
        pickle.dump(vectorizer, f)
    print("Saved as 'essay_classifier_model.pkl' and 'essay_vectorizer.pkl'")

if __name__ == "__main__":
    main()
