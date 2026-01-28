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
