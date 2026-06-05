import os
import sys
import glob
import cv2
import numpy as np
import argparse

def process_directory(directory: str, is_mono: bool = False):
    """
    Converts all fast-written .npy raw frames in a directory to .jpg images.
    If is_mono is True, treats the raw data as grayscale.
    Otherwise, assumes Player One RAW8 (Bayer RGGB) and debayers to BGR.
    """
    if not os.path.isdir(directory):
        print(f"Error: Directory '{directory}' not found.")
        sys.exit(1)
        
    npy_files = glob.glob(os.path.join(directory, "*.npy"))
    if not npy_files:
        print(f"No .npy files found in {directory}")
        return
        
    print(f"Found {len(npy_files)} raw frames. Processing...")
    
    for npy_path in npy_files:
        try:
            raw_frame = np.load(npy_path)
            
            if is_mono:
                # Direct save for mono sensors or PiHQ grayscale fallback
                bgr_frame = cv2.cvtColor(raw_frame, cv2.COLOR_GRAY2BGR)
            else:
                # Debayer Player One RAW8 (usually RGGB)
                bgr_frame = cv2.cvtColor(raw_frame, cv2.COLOR_BAYER_RG2BGR)
                
            jpg_path = npy_path.replace(".npy", ".jpg")
            cv2.imwrite(jpg_path, bgr_frame)
            
            # Optional: delete the raw file to save space after successful conversion
            # os.remove(npy_path)
            
            print(f"Converted: {os.path.basename(npy_path)} -> {os.path.basename(jpg_path)}")
        except Exception as e:
            print(f"Failed to process {npy_path}: {e}")
            
    print("Post-processing complete.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert RoboCam 3.1 fast-write raw .npy frames to .jpg")
    parser.add_argument("directory", help="Path to the experiment output directory containing .npy files")
    parser.add_argument("--mono", action="store_true", help="Treat raw frames as monochrome (skip debayering)")
    
    args = parser.parse_args()
    process_directory(args.directory, args.mono)
