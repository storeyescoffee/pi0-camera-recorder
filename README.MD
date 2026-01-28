# Raspberry Pi Video Recording Script

## Overview

This script is designed for recording video on a Raspberry Pi using the `rpicam-vid` utility to capture H.264 video, and `ffmpeg` to segment the video into smaller files. It runs continuously within a specified time range (from 07:00 to 22:00) and saves the video files in a local directory (`$HOME/recordings`). The script splits the video into segments of 5 minutes (300 seconds) each.

Additionally, a `rename.sh` script renames the recorded files based on their creation (birth) time, and a cron job schedule automates the recording, renaming, and cleanup processes.

## Features

- Captures video from the Raspberry Pi camera.
- Configured to record in 720p (1280x720) resolution with a bitrate of 2 Mbps.
- Segments the video into files of 5-minute duration.
- Records during specific hours (07:00 to 22:00).
- Saves the video files to a specified directory (`$HOME/recordings`).
- Renames video files based on their birth time to a more readable format.
- Automatically cleans up old recordings and manages processes via cron jobs.

## Requirements

- **Raspberry Pi** with a compatible camera module.
- **rpicam-vid** utility (used for recording H.264 video).
- **ffmpeg** utility (used for video segmentation and conversion).
- **cron** utility (for scheduling tasks).

## Script Configuration

Below are the configurable parameters in the script:

| Parameter             | Description                                                  | Default Value    |
|-----------------------|--------------------------------------------------------------|------------------|
| `BASE_DIR`            | Directory to save video recordings.                          | `$HOME/recordings` |
| `SEGMENT_SECONDS`     | Duration of each video segment in seconds.                   | 300 seconds (5 minutes) |
| `BITRATE`             | Bitrate of the video recording in bits per second.           | 2 Mbps (2097152 bps) |
| `FPS`                 | Frames per second of the video.                              | 25 FPS           |
| `WIDTH`               | Width of the video in pixels.                                | 1280             |
| `HEIGHT`              | Height of the video in pixels.                               | 720              |
| `GOP`                 | Group of pictures (GOP) size, affecting video quality.       | 125 (5s GOP)     |

## Script Breakdown

### **record_video.sh**

1. **Set Up Configuration**  
   The script starts by defining the configuration parameters, such as video resolution, bitrate, and segment duration. The `$BASE_DIR` is set to the recordings directory (`$HOME/recordings`).

2. **Time-Based Recording**  
   The script checks the current hour and only starts recording between 07:00 and 22:00. If the current hour is outside this range, the script exits immediately.

3. **Recording Loop**  
   The script enters an infinite loop where it:
   - Uses `rpicam-vid` to record video from the Raspberry Pi camera with the specified settings (e.g., bitrate, resolution, frame rate).
   - The video is then passed to `ffmpeg`, which segments it into smaller files of 5 minutes each (`SEGMENT_SECONDS`).
   - The segmented videos are saved in the `$BASE_DIR` directory, with filenames like `video_000001.mp4`, `video_000002.mp4`, etc.

4. **Restart After Stopping**  
   If the video capture stops for any reason, the script will wait for 2 seconds and then restart the recording process.

### **rename.sh**

The `rename.sh` script is responsible for renaming the recorded video files based on their creation (birth) time, making the filenames more readable and sorted by date and time.

```bash
#!/bin/bash

BASE_DIR=/home/m0hcine24/recordings

for file in $BASE_DIR/video_*.mp4; do

    # Skip if ffmpeg (or anything) is writing to the file
    if lsof "$file" 2>/dev/null | awk '{print $1}' | grep -q 'ffmpeg'; then
        echo "⏭ Skipping (writing): $file"
        continue
    fi

    # Get birth time, fallback to modify time if unavailable
    birth=$(stat -c %w "$file")
    [[ "$birth" == "-" ]] && birth=$(stat -c %y "$file")

    new_name=$BASE_DIR/$(date -d "$birth" +"%d%m%Y_%H%M%S").mp4

    echo "Renaming: $file → $new_name"
    mv -- "$file" "$new_name"
done
