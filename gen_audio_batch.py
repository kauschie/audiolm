from audiolm_pytorch import AudioLM, SemanticTransformer, CoarseTransformer, FineTransformer, HubertWithKmeans, EncodecWrapper
import torch
import torchaudio
import argparse
import math

# Model paths
hubert_checkpoint_path = "./models/hubert_base_ls960.pt"
hubert_kmeans_path = "./models/hubert_base_ls960_L9_km500.bin"

sem_step = 50000
coarse_step = 50000
fine_step = 50000

# sem_path = f"./results/semantic.transformer.{sem_step}.final.pt"
# coarse_path = f"./results/coarse.transformer.{coarse_step}.final.pt"
# fine_path = f"./results/fine.transformer.{fine_step}.final.pt"

sem_path = "./great/p1_results/semantic.transformer.25000.pt"
coarse_path = "./great/p1_results/coarse.transformer.29219.terminated_session.pt"
fine_path = "./great/p1_results/fine.transformer.24245.terminated_session.pt"



# Initialize models
encodec = EncodecWrapper()
wav2vec = HubertWithKmeans(
    checkpoint_path=hubert_checkpoint_path, 
    kmeans_path=hubert_kmeans_path
    ).cuda()

semantic_transformer = SemanticTransformer(
    num_semantic_tokens=wav2vec.codebook_size, 
    dim=1024, 
    depth=12, 
    heads=16
    ).cuda()
semantic_transformer.load(sem_path)

coarse_transformer = CoarseTransformer(
    num_semantic_tokens=wav2vec.codebook_size, 
    codebook_size=1024, 
    num_coarse_quantizers=3, 
    dim=1024, 
    depth=6, 
    heads=16
    ).cuda()
coarse_transformer.load(coarse_path)

fine_transformer = FineTransformer(
    num_coarse_quantizers=3, 
    num_fine_quantizers=5, 
    codebook_size=1024, 
    dim=1024, 
    depth=6, 
    heads=16
    ).cuda()
fine_transformer.load(fine_path)

audiolm = AudioLM(
    wav2vec=wav2vec, 
    codec=encodec, 
    semantic_transformer=semantic_transformer, 
    coarse_transformer=coarse_transformer, 
    fine_transformer=fine_transformer, 
    unique_consecutive=False
    )

# def main():
#     # Parse command-line arguments
#     parser = argparse.ArgumentParser(description="Generate stitched AudioLM output with batch processing.")
#     parser.add_argument("--duration", type=float, required=True, help="Desired output duration in seconds")
#     parser.add_argument("--output", type=str, default="stitched_audio", help="Output filename (without .wav)")
#     parser.add_argument("--prime_wave", type=str, default=None, help="Path to WAV file to use as an initial seed")
#     parser.add_argument("--debug", action="store_true", help="Enable debug mode to save individual segments")
#     parser.add_argument("--batch_size", type=int, default=2, help="Batch size for parallel audio generation")

#     args = parser.parse_args()
#     desired_clip_duration = args.duration  # User-defined output length in seconds
#     output_file = args.output
#     prime_wave_path = args.prime_wave
#     debug_mode = args.debug
#     batch_size = args.batch_size

#     # Constants
#     sample_rate = 24000
#     output_length = sample_rate * 4  # 4-second chunks per generation
#     overlap_length = int(sample_rate * 1)  # 1 second overlap
#     fade_out_duration = int(sample_rate * 0.5)  # 0.5s fade out at the end

#     generated_audio = [[] for _ in range(batch_size)]  # Store audio sequences for each batch
#     debug_counter = 1  # Counter for debug file names

#     # Compute fade-in and fade-out values **outside the loop** (optimization)
#     fade_in = torch.linspace(0, 1, overlap_length).unsqueeze(0)
#     fade_out = torch.linspace(1, 0, overlap_length).unsqueeze(0)
#     t = torch.linspace(0, 1, fade_out_duration)
#     cosine_fade_out = (1 + torch.cos(math.pi * t)) / 2
#     final_fade_out = cosine_fade_out.unsqueeze(0)

#     # Load prime_wave if specified
#     prime_wave = None
#     if prime_wave_path:
#         print(f"Loading prime wave from {prime_wave_path}...")
#         prime_wave, prime_sample_rate = torchaudio.load(prime_wave_path)

#         # Resample if necessary
#         if prime_sample_rate != sample_rate:
#             print(f"Resampling prime wave from {prime_sample_rate}Hz to {sample_rate}Hz...")
#             resampler = torchaudio.transforms.Resample(orig_freq=prime_sample_rate, new_freq=sample_rate)
#             prime_wave = resampler(prime_wave)

#         # Ensure correct shape and move to GPU
#         if prime_wave.dim() == 1:
#             prime_wave = prime_wave.unsqueeze(0)
#         prime_wave = prime_wave.cuda()

#     # Generate first batch of clips
#     output = audiolm(batch_size=batch_size, max_length=output_length, prime_wave=prime_wave, prime_wave_input_sample_hz=sample_rate)

#     # Ensure batch outputs are tensors
#     if isinstance(output, list):
#         output = [o.cpu().unsqueeze(0) if o.dim() == 1 else o.cpu() for o in output]

#     # Store initial batch of audio (excluding the overlap)
#     for i in range(batch_size):
#         generated_audio[i].append(output[i][:, :-overlap_length])

#         if debug_mode:
#             torchaudio.save(f"piece{debug_counter}-{i}.wav", output[i][:, :-overlap_length], sample_rate)
#             debug_counter += 1

#     # Continue generating until we reach the desired duration
#     while sum([chunk.shape[1] for chunk in generated_audio[0]]) < (desired_clip_duration * sample_rate):
#         cur_length = sum([chunk.shape[1] for chunk in generated_audio[0]])
#         print(f"Current length: {cur_length/sample_rate} seconds")

#         # Extract semantic token IDs for each waveform in the batch **individually**
#         prime_id_list = []
#         for i, out in enumerate(output):
#             last_segment = out[:, -overlap_length:].to("cuda")  # Move to GPU
#             print(f"[DEBUG] Processing batch {i} - Last segment shape: {last_segment.shape}, Device: {last_segment.device}")

#             # Convert to semantic token IDs
#             prime_ids = audiolm.semantic.wav2vec(last_segment, flatten=False, input_sample_hz=sample_rate)
#             print(f"[DEBUG] Generated prime_ids {i} - Shape: {prime_ids.shape}, Device: {prime_ids.device}")

#             prime_id_list.append(prime_ids)

#         # Stack all processed token sequences
#         prime_ids = torch.stack(prime_id_list).squeeze(1)  # Remove extra dim
#         print(f"[DEBUG] Reshaped prime_ids shape: {prime_ids.shape}")

#         # Pass tokenized IDs to audiolm instead of raw waveform
#         next_output = audiolm(batch_size=batch_size, max_length=output_length, prime_ids=prime_ids)

#         # Ensure correct format
#         if isinstance(next_output, list):
#             next_output = [o.cpu().unsqueeze(0) if o.dim() == 1 else o.cpu() for o in next_output]

#         for i in range(batch_size):
#             # Smooth the overlap region for batch i
#             transition_start = output[i][:, -overlap_length:] * fade_out + next_output[i][:, :overlap_length] * fade_in

#             # Append transition and the non-overlapping portion of next_output
#             generated_audio[i].append(transition_start)
#             generated_audio[i].append(next_output[i][:, overlap_length:])

#             if debug_mode:
#                 torchaudio.save(f"piece{debug_counter}-{i}.wav", transition_start, sample_rate)
#                 debug_counter += 1
#                 torchaudio.save(f"piece{debug_counter}-{i}.wav", next_output[i][:, overlap_length:], sample_rate)
#                 debug_counter += 1

#         # Update output for next iteration
#         output = next_output

#     # Concatenate all audio clips **before applying fade-out**
#     final_audio = [torch.cat(audio, dim=-1) for audio in generated_audio]

#     # Apply **cosine fade-out** to the last `fade_out_duration` samples for each batch
#     for i in range(batch_size):
#         fade_section_start = max(final_audio[i].shape[1] - fade_out_duration, 0)
#         final_audio[i][:, fade_section_start:] *= final_fade_out[:, -final_audio[i].shape[1] + fade_section_start:]

#         # Save the final stitched audio file for each batch item
#         torchaudio.save(f"{output_file}-{i}.wav", final_audio[i], sample_rate)
#         print(f"Saved final stitched audio to {output_file}-{i}.wav with duration {desired_clip_duration} seconds.")


def main():
    # Parse command-line arguments
    parser = argparse.ArgumentParser(description="Generate stitched AudioLM output with batch processing.")
    parser.add_argument("--duration", type=float, required=True, help="Desired output duration in seconds")
    parser.add_argument("--output", type=str, default="stitched_audio", help="Output filename (without .wav)")
    parser.add_argument("--prime_wave", type=str, default=None, help="Path to WAV file to use as an initial seed")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode to save individual segments")
    parser.add_argument("--batch_size", type=int, default=2, help="Batch size for parallel audio generation")

    args = parser.parse_args()
    desired_clip_duration = args.duration
    output_file = args.output
    prime_wave_path = args.prime_wave
    debug_mode = args.debug
    batch_size = args.batch_size

    sample_rate = 24000
    output_length = sample_rate * 4  
    overlap_length = int(sample_rate * 1)
    fade_out_duration = int(sample_rate * 0.5)

    generated_audio = [[] for _ in range(batch_size)]
    debug_counter = 1

    fade_in = torch.linspace(0, 1, overlap_length).unsqueeze(0)
    fade_out = torch.linspace(1, 0, overlap_length).unsqueeze(0)
    t = torch.linspace(0, 1, fade_out_duration)
    cosine_fade_out = (1 + torch.cos(math.pi * t)) / 2
    final_fade_out = cosine_fade_out.unsqueeze(0)

    prime_wave = None
    if prime_wave_path:
        prime_wave, prime_sample_rate = torchaudio.load(prime_wave_path)
        if prime_sample_rate != sample_rate:
            resampler = torchaudio.transforms.Resample(orig_freq=prime_sample_rate, new_freq=sample_rate)
            prime_wave = resampler(prime_wave)
        if prime_wave.dim() == 1:
            prime_wave = prime_wave.unsqueeze(0)
        prime_wave = prime_wave.cuda()

    output = audiolm(batch_size=batch_size, max_length=output_length, prime_wave=prime_wave, prime_wave_input_sample_hz=sample_rate)
    output = [o.cpu().unsqueeze(0) if o.dim() == 1 else o.cpu() for o in output]

    for i in range(batch_size):
        generated_audio[i].append(output[i][:, :-overlap_length])

    while sum([chunk.cpu().shape[1] for chunk in generated_audio[0]]) < (desired_clip_duration * sample_rate):
        cur_length = sum([chunk.shape[1] for chunk in generated_audio[0]])
        print(f"Current length: {cur_length/sample_rate} seconds")

        prime_ids = []
        for i in range(batch_size):
            last_segment = output[i][:, -overlap_length:].to("cuda")
            prime_ids.append(audiolm.semantic.wav2vec(last_segment, flatten=False, input_sample_hz=sample_rate))

        prime_ids = torch.cat(prime_ids, dim=0)

        next_output = audiolm(batch_size=batch_size, max_length=output_length, prime_ids=prime_ids)
        next_output = [o.cpu().unsqueeze(0) if o.dim() == 1 else o.cpu() for o in next_output]

        for i in range(batch_size):
            # Ensure overlap_length is within valid range for generated_audio[i][-1]
            last_clip_length = generated_audio[i][-1].shape[1]
            valid_overlap_length = min(last_clip_length, overlap_length)

            # Correctly extract transition sections
            last_segment = generated_audio[i][-1][:, -valid_overlap_length:].cuda()
            next_segment = next_output[i][:, :valid_overlap_length].cuda()

            # Adjust fade tensors to match segment size
            fade_in_adj = fade_in[:, :valid_overlap_length].cuda()
            fade_out_adj = fade_out[:, :valid_overlap_length].cuda()

            # Apply smoothing transition with adjusted fade tensors
            transition_start = last_segment * fade_out_adj + next_segment * fade_in_adj

            # Store new audio sequence
            # generated_audio[i].append(transition_start)
            generated_audio[i].append(next_output[i][:, valid_overlap_length:].cuda())


        output = next_output

    # Move generated_audio to CPU before concatenation
    final_audio = [torch.cat([chunk.cpu() for chunk in audio], dim=-1) for audio in generated_audio]


    for i in range(batch_size):
        fade_section_start = max(final_audio[i].shape[1] - fade_out_duration, 0)
        final_audio[i][:, fade_section_start:] *= final_fade_out[:, -final_audio[i].shape[1] + fade_section_start:]

        # Move tensor to CPU before saving
        final_audio_cpu = final_audio[i].cpu()

        # Save to file
        torchaudio.save(f"{output_file}-{i}.wav", final_audio_cpu, sample_rate)

        print(f"Saved final stitched audio to {output_file}-{i}.wav with duration {desired_clip_duration} seconds.")



if __name__ == "__main__":
    main()
