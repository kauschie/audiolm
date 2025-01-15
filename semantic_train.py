import os
import time
from datetime import datetime
import signal
import pickle
import torch
from torch.utils.data import Dataset
from audiolm_pytorch import HubertWithKmeans, SemanticTransformer, SemanticTransformerTrainer
from audiolm_pytorch.trainer import dict_values_to_device
from tensorboardX import SummaryWriter

# Paths to models and dataset
hubert_checkpoint_path = './models/hubert_base_ls960.pt'
hubert_kmeans_path = './models/hubert_base_ls960_L9_km500.bin'
dataset_path = './dbo'
results_folder = './results'  # Results directory
save_path = os.path.join(results_folder, 'semantic_transformer_final.pt')  # Save final model here
log_file_path = os.path.join(results_folder, 'training_logs.txt')
train_split_path = os.path.join(results_folder, 'train_split.pkl')
valid_split_path = os.path.join(results_folder, 'valid_split.pkl')

# Initialize TensorBoard writer
writer = SummaryWriter(logdir='./logs')

# Initialize HubertWithKmeans
wav2vec = HubertWithKmeans(
    checkpoint_path=hubert_checkpoint_path,
    kmeans_path=hubert_kmeans_path
).cuda()

# Define and initialize the Semantic Transformer
semantic_transformer = SemanticTransformer(
    num_semantic_tokens=wav2vec.codebook_size,  # From HubertWithKmeans
    dim=1024,  # Transformer dimensionality
    depth=6,  # Number of transformer layers
    flash_attn=True,  # Use Flash Attention for efficiency
    ff_dropout=0.1,     # add some dopout to reduce overfitting
    attn_dropout=0.1    # add some dropout to reduce overfitting
).cuda()

# Load or create dataset splits
def load_splits():
    if os.path.exists(train_split_path) and os.path.exists(valid_split_path):
        choice = None
        while choice not in ['y', 'n']:
            try:
                choice = input("Data Splits found.\nDo you wish to load Previously saved training and validation Data? (y/n): ").strip().lower()
            except Exception as e:
                choice = None
                print(f"Error getting input: {e}")
                print("Please enter y or n only")
        if choice != 'y':
            print("Continuing without loading existing dataset splits...")
            return None, None
        print("Loading existing dataset splits...")
        with open(train_split_path, 'rb') as f:
            train_split = pickle.load(f)
        with open(valid_split_path, 'rb') as f:
            valid_split = pickle.load(f)
        return train_split, valid_split
    
    else:
        return None, None

train_split, valid_split = load_splits()

# Trainer for the Semantic Transformer
training_temp = 60

if train_split is not None and valid_split is not None:
    semantic_trainer = SemanticTransformerTrainer(
        transformer=semantic_transformer,
        wav2vec=wav2vec,  # HubertWithKmeans model
        dataset=train_split,  # Preloaded training dataset
        valid_dataset=valid_split,  # Preloaded validation dataset
        batch_size=4,  # Adjust based on GPU memory
        grad_accum_every=8,  # Gradient accumulation steps
        data_max_length=240000,  # Max number of audio samples (24 kHz * 10 seconds)
        num_train_steps=training_temp,  # Reduced number of training steps for timing experiment
        results_folder=results_folder,  # Specify custom results folder
        save_model_every=1_000_000,  # Disable automatic saving
        save_results_every=1_000_000  # Disable automatic saving
    )
else:
    semantic_trainer = SemanticTransformerTrainer(
        transformer=semantic_transformer,
        wav2vec=wav2vec,  # HubertWithKmeans model
        folder=dataset_path,  # Path to your training data
        batch_size=4,  # Adjust based on GPU memory
        grad_accum_every=8,  # Gradient accumulation steps
        data_max_length=240000,  # Max number of audio samples (24 kHz * 10 seconds)
        num_train_steps=training_temp,  # Reduced number of training steps for timing experiment
        results_folder=results_folder,  # Specify custom results folder
        save_model_every=1_000_000,  # Disable automatic saving
        save_results_every=1_000_000  # Disable automatic saving
    )

    # Save the generated dataset splits
    print("Saving newly created dataset splits...")
    with open(train_split_path, 'wb') as f:
        pickle.dump(semantic_trainer.ds, f)
    with open(valid_split_path, 'wb') as f:
        pickle.dump(semantic_trainer.valid_ds, f)
    print(f"Dataset splits saved: {len(semantic_trainer.ds)} training samples, {len(semantic_trainer.valid_ds)} validation samples.")

# Check for existing checkpoints
checkpoint_files = [f for f in os.listdir(results_folder) if f.endswith('.pt')]
if checkpoint_files:
    print("Existing checkpoints found:")
    for i, file in enumerate(checkpoint_files):
        print(f"{i + 1}: {file}")
    choice = input("Do you want to load a checkpoint? Enter the number or 'n' to start fresh: ")
    if choice.isdigit() and 1 <= int(choice) <= len(checkpoint_files):
        checkpoint_path = os.path.join(results_folder, checkpoint_files[int(choice) - 1])
        print(f"Loading checkpoint from {checkpoint_path}...")
        semantic_trainer.load(checkpoint_path)
        print(f"Checkpoint {checkpoint_path} loaded successfully.")
    else:
        print("Starting fresh without loading a checkpoint.")
else:
    print("No checkpoints found. Starting fresh.")

# Define a signal handler for saving on interrupt
def handle_interrupt(signal_received, frame):
    print("\nTraining interrupted by user.")
    save_prompt = input("Do you want to save the current model and results? (y/n): ").strip().lower()
    steps = int(semantic_trainer.steps.item())
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if save_prompt == 'y':
        term_path = str(semantic_trainer.results_folder / f'semantic.transformer.{steps}.terminated_session.pt')
        semantic_trainer.save(term_path)
        semantic_trainer.print(f"{steps}: saving model to {term_path}")
        try:
            with open(log_file_path, 'a') as log_file:
                log_file.write(f"[{timestamp}] Step {steps}: Training interrupted by user. Model saved to {term_path}.\n")
        except Exception as e:
            print(f"Failed to write to log file: {e}")
    else:
        semantic_trainer.print("Progress not saved.")
        try:
            with open(log_file_path, 'a') as log_file:
                log_file.write(f"[{timestamp}] Step {steps}: Training interrupted by user. Progress not saved.\n")
        except Exception as e:
            print(f"Failed to write to log file: {e}")

    exit(0)


signal.signal(signal.SIGINT, handle_interrupt)

# Define a logging function
def log_fn(logs):
    validation_interval = 10
    model_save_interval = 100

    steps = int(semantic_trainer.steps.item())-1  # Get the current step from the trainer (trainer adds 1 before calling log function)
    loss = logs.get('loss', None)

    # Calculate validation loss manually
    valid_loss = None
    if semantic_trainer.is_main and (steps > 0) and (steps % validation_interval) == 0:  # Example condition for validation
        valid_loss = 0
        unwrapped_model = semantic_trainer.accelerator.unwrap_model(semantic_trainer.train_wrapper)
        for _ in range(semantic_trainer.average_valid_loss_over_grad_accum_every):
            data_kwargs = semantic_trainer.data_tuple_to_kwargs(next(semantic_trainer.valid_dl_iter))
            data_kwargs = dict_values_to_device(data_kwargs, unwrapped_model.device)

            with torch.inference_mode():
                unwrapped_model.eval()
                valid_loss += unwrapped_model(**data_kwargs, return_loss=True)

        valid_loss = valid_loss.clone()
        valid_loss /= semantic_trainer.average_valid_loss_over_grad_accum_every

        semantic_trainer.print(f'{steps}: valid loss {valid_loss}')
        semantic_trainer.accelerator.log({"valid_loss": valid_loss}, step=steps)

    if semantic_trainer.is_main and (steps > 0) and (steps % model_save_interval) == 0:
        model_path = str(semantic_trainer.results_folder / f'semantic.transformer.temp.pt')
        semantic_trainer.save(model_path)
        semantic_trainer.print(f'{steps}: saving model to {str(semantic_trainer.results_folder)}')

    # Add timestamp for log entries
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Log to file and TensorBoard
    with open(log_file_path, 'a') as log_file:
        if loss is not None:
            log_file.write(f"[{timestamp}] Step {steps}: Training Loss: {loss}\n")
            writer.add_scalar("Training Loss", loss, steps)

        if valid_loss is not None:
            log_file.write(f"[{timestamp}] Step {steps}: Validation Loss: {valid_loss}\n")
            writer.add_scalar("Validation Loss", valid_loss, steps)

# Measure training time
start_time = time.time()

# Train the Semantic Transformer
print("Starting training for the Semantic Transformer...")
semantic_trainer.train(log_fn=log_fn)

# Save the final model explicitly
save_path = os.path.join(results_folder, f'semantic.transformer.{int(semantic_trainer.steps.item())-1}.final.pt')  # Save final model here
semantic_trainer.save(save_path)
print(f"Final model saved to {save_path}")

end_time = time.time()
training_time = end_time - start_time

# Log the final training results
final_loss = semantic_trainer.steps.item()
writer.add_scalar("Final Training Loss", final_loss, int(semantic_trainer.steps.item()))

# Close TensorBoard writer
writer.close()

print(f"Training complete. Checkpoints and logs saved to {results_folder}")
print(f"Loss logs saved to {log_file_path}")
print(f"Total training time: {training_time:.2f} seconds")
