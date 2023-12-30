# furarchiver_Open

This repository is aimed at recreating furarchiver with additional features I find useful. 

## Features
- **Pulling Deleted FA Posts**: Retrieve deleted FA posts via a backend (accessed over Tor).
- **Integration with E621**: Option to pull posts from E621.
- **Quality Checks for FA Posts**: Re-pull posts from FA if the backend downloaded a bad version.
- **Tag-Based Search**: Utilize tags from E621 when available, or apply auto-tagging.
- **Score-Based Search**: Search posts based on E621 score, or use an AI-generated score if not available from E621.
- **Aesthetic Score**: Evaluate posts based on an AI-generated aesthetic score.
- **Bulk Download**: Download content en masse from your local library for sharing.
- **Manual Upload to Artists**: Ability to upload images manually to artists.

## Basic Setup Steps
1. **Install Dependencies**: Run `pip install -r requirements.txt` to install all required packages.
2. **Tor Connection**: The easiest way to connect to Tor is to download the Tor web browser and let it run in the background.
3. **Model Files**: Place model files in the `models/` directory.
   - **3A**: Recommended model e621-l14-rhoLoss (requires both json and ckpt) from [here](https://github.com/feffy380/improved-aesthetic-predictor/tree/main/models).
   - **3B**: For aesthetics, use SAC logo from [this source](https://github.com/christophschuhmann/improved-aesthetic-predictor).
   - **3C**: For tagging, I use the balanced model from [Hugging Face](https://huggingface.co/Thouph/experimental_efficientnetv2_m_8035/tree/main).
