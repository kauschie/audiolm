import os
import sys
import shutil
import time
import signal
import pickle
import logging
import torch
from audiolm_pytorch import HubertWithKmeans, SemanticTransformer, SemanticTransformerTrainer
from audiolm_pytorch.trainer import dict_values_to_device
from tensorboardX import SummaryWriter

def setup_logger(level=logging.INFO):
    """
    Sets up a custom logger with a format similar to logging.basicConfig,
    forcefully replacing any existing handlers.
    
    Args:
        level (int): Logging level (e.g., logging.DEBUG, logging.INFO).
    
    Returns:
        logging.Logger: Configured logger instance.
    """
    # Create or get the logger
    logger = logging.getLogger("semantic_training.log")
    logger.setLevel(level)
    
    # Remove any existing handlers
    logger.handlers.clear()
    
    # File handler
    file_handler = logging.FileHandler(log_file_path, mode='a')  # Append mode
    file_handler.setLevel(level)
    file_formatter = logging.Formatter(
        '%(asctime)s - %(message)s'
    )
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    # Prevent logs from propagating to the root logger
    logger.propagate = False
    
    return logger

# Configure logging
log_dir = './logs/sem'
os.makedirs(log_dir, exist_ok=True)
log_file_path = os.path.join(log_dir, 'semantic_training.log')
logger = setup_logger()
logger.info(f"Logger initiated, Semantic Trainer Program Running")

# Paths to models and dataset
hubert_checkpoint_path = './models/hubert_base_ls960.pt'
hubert_kmeans_path = './models/hubert_base_ls960_L9_km500.bin'
# dataset_path = "./p2-data/smallest_test_16khz"
# dataset_path = "./p2-data/micro_test_16khz"
# dataset_path = "/home/mkausch/dev/audiolm/p1_data/small"  # p1 20,000 songs
dataset_path = "/home/mkausch/dev/audiolm/p2-data/p2_4s_16k"  # p2 868,603 songs
results_folder = './results'  # Results directory

# Initialize TensorBoard writer
writer = SummaryWriter(logdir=log_dir)

# Initialize HubertWithKmeans
wav2vec = HubertWithKmeans(
    checkpoint_path=hubert_checkpoint_path,
    # checkpoint_path=None,
    kmeans_path=hubert_kmeans_path,
    # use_mert=True
)

print(f"wav2vec.target_sample_hz: {wav2vec.target_sample_hz}")
logger.info(f"wav2vec.target_sample_hz: {wav2vec.target_sample_hz}")

# Define and initialize the Semantic Transformer

"""
Hyperparameters Taken from 

The following are generated outputs from the Semantic Transformer with 12 layers, 
16 attention heads, 
a dimension of 1024, 
drop-out of 0.1, 
batch size of 128, 
gradient accumulation of 16. 
Default settings (build 0.0.57) for everything else. 
Trained on a single GPU for a few days.

"""

# temp_dim = 1024
temp_dim = 1024
temp_depth = 12
temp_heads = 16
semantic_transformer = SemanticTransformer(
    num_semantic_tokens=wav2vec.codebook_size,  # From HubertWithKmeans
    dim=temp_dim,  # 1024 Transformer dimensionality
    depth=temp_depth,  # Number of transformer layers
    heads=temp_heads,
    # flash_attn=True,  # Use Flash Attention for efficiency
).cuda()

# Trainer for the Semantic Transformer
training_max = 50001
model_save = 5000
results_save = 50001
temp_max_length = 16000*4
# temp_data_max_length_seconds = 2

logger.info(f"Transformers initiated with the following parameters:")

semantic_trainer = SemanticTransformerTrainer(
    transformer=semantic_transformer,
    wav2vec=wav2vec,  # HubertWithKmeans model
    folder=dataset_path,  
    force_clear_prev_results=False,
    batch_size=32,  # Adjust based on GPU memory
    grad_accum_every=4,  # Gradient accumulation steps
    data_max_length=temp_max_length,  # Max number of audio samples (16 kHz * 4 seconds)
    num_train_steps=training_max,  # Reduced number of training steps for timing experiment
    results_folder=results_folder,  # Specify custom results folder
    save_model_every=model_save,  # Disable automatic saving
    save_results_every=results_save  # Disable automatic saving
)


logger.info(f"batch_size: {semantic_trainer.batch_size}")
logger.info(f"grad_accum_every: {semantic_trainer.grad_accum_every}")
logger.info(f"data_max_length: {temp_max_length}")
logger.info(f"dim: {temp_dim}")
logger.info(f"depth: {temp_depth}")
logger.info(f"heads: {temp_heads}")
logger.info(f"num_semantic_tokens: {semantic_transformer.num_semantic_tokens}")
logger.info(f"dataset: {dataset_path}")


# Check for existing checkpoints
checkpoint_files = [f for f in os.listdir(results_folder) if f.endswith('.pt') and 'semantic' in f]
if checkpoint_files:
    print("Existing checkpoints found:")
    for i, file in enumerate(checkpoint_files):
        print(f"{i + 1}: {file}")
    choice = input("Do you want to load a checkpoint? Enter the number or 'n' to start fresh: ")
    if choice.isdigit() and 1 <= int(choice) <= len(checkpoint_files):
        checkpoint_path = os.path.join(results_folder, checkpoint_files[int(choice) - 1])
        print(f"Loading checkpoint from {checkpoint_path}...")
        logger.info(f"Loading checkpoint from {checkpoint_path}...")
        semantic_trainer.load(checkpoint_path)
        print(f"Checkpoint {checkpoint_path} loaded successfully.")
        logger.info(f"Checkpoint {checkpoint_path} loaded successfully.")
    else:
        print("Starting fresh without loading a checkpoint.")
        logger.info("Starting fresh without loading a checkpoint.")
else:
    logger.info("No checkpoints found. Starting fresh.")

def cleanup_cuda():
    torch.cuda.empty_cache()
    print("CUDA memory cache cleared.")

def save_checkpoint(auto_save=False):
    global semantic_trainer
    steps = int(semantic_trainer.steps.item())

    if auto_save:
        term_path = str(semantic_trainer.results_folder / f'semantic.transformer.{steps}.terminated_session.pt')
        semantic_trainer.save(term_path)
        logger.info(f"{steps}: Auto-saving model to {term_path}")
    else:
        save_prompt = input("Do you want to save the current model and results? (y/n): ").strip().lower()
        if save_prompt == 'y':
            term_path = str(semantic_trainer.results_folder / f'semantic.transformer.{steps}.terminated_session.pt')
            semantic_trainer.save(term_path)
            logger.info(f"{steps}: Saving model to {term_path}")
        else:
            logger.info("Progress not saved.")


# Define a signal handler for saving on interrupt
def handle_interrupt(signal_received, frame):
    print("\nTraining interrupted by user.")
    save_checkpoint()
    cleanup_cuda()
    sys.exit(0)

signal.signal(signal.SIGINT, handle_interrupt)

def handle_exception(e, move_bad_file=None):
    """Handles failure by logging, saving a checkpoint, cleaning up CUDA, and exiting."""
    logger.info(f"\nError encountered: {e}")
    logger.info("Saving checkpoint and attempting cleanup.")
    
    save_checkpoint(auto_save=True)  # Save your model
    cleanup_cuda()  # Cleanup CUDA memory

    if move_bad_file:
        bad_dir = "p2-data/bad/"
        os.makedirs(bad_dir, exist_ok=True)  # Ensure the directory exists
        bad_file_path = os.path.join(bad_dir, os.path.basename(move_bad_file))
        shutil.move(move_bad_file, bad_file_path)
        logger.info(f"Moved bad file to {bad_file_path}")

    sys.exit(1)  # Exit with failure code

# Define a logging function
def log_fn(logs):
    steps = int(semantic_trainer.steps.item()) - 1  # Get the current step from the trainer (trainer adds 1 before calling log function)
    loss = logs.get('loss', None)

    # Log to Log and TensorBoard
    if loss is not None:
        logger.info(f"Step {steps}: Training Loss: {loss}")
        writer.add_scalar("Training Loss", loss, steps)
        

# Measure training time
start_time = time.time()

# Train the Semantic Transformer
print("Starting training for the Semantic Transformer...")
logger.info("Starting training for the Semantic Transformer...")

try:
    # semantic_trainer.train()
    semantic_trainer.train(log_fn=log_fn)
except RuntimeError as e:
    if "CUDA error" in str(e):
        handle_exception(e)
    else:
        raise   # reraise exception
except AssertionError as e:
    if "empty" in str(e):
        bad_file = None
        message = str(e)
        if "(" in message and ")" in message:
            bad_file = message.split("(")[1].split(")")[0] # get file path inside parens
            handle_exception(e, move_bad_file=bad_file)

    else:
        raise

# Save the final model explicitly
save_path = os.path.join(results_folder, f'semantic.transformer.{int(semantic_trainer.steps.item())-1}.final.pt')  # Save final model here

semantic_trainer.save(save_path)
print(f"Final model saved to {save_path}")
logger.info(f"Final model saved to {save_path}")

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
logger.info(f"Training complete. Checkpoints and logs saved to {results_folder}")
logger.info(f"Total training time: {training_time:.2f} seconds")
