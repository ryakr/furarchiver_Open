import torch
from PIL import Image
from transformers import AutoProcessor, CLIPModel, logging
from Scorer.MLP import MLP
import json
import os

class AestheticPredictor:
    def __init__(self, model_path, clip_model_name, device='default', raw=False):
        self.model_path = model_path
        self.clip_model_name = clip_model_name
        self.device = 'cuda' if torch.cuda.is_available() else 'cpu' if device == 'default' else device
        self.raw = raw
        self.y_stats = self._load_y_stats()
        self._setup()

    def _setup(self):
        logging.set_verbosity_error()
        print("Loading CLIP model...")
        self.clip_model = CLIPModel.from_pretrained(self.clip_model_name).to(self.device)
        self.preprocess = AutoProcessor.from_pretrained(self.clip_model_name)
        self.mlp_model = self._load_mlp_model()

    def _load_mlp_model(self):
        dim = self.clip_model.projection_dim
        print("Loading MLP model...")
        model = MLP(dim)
        sd = torch.load(self.model_path)
        if "state_dict" in sd:
            sd = sd["state_dict"]
        model.load_state_dict(sd)
        model.to(self.device)
        model.eval()
        return model

    def _load_y_stats(self):
        try:
            print("Loading y_stats...")
            path = os.path.splitext(self.model_path)[0] + ".json"
            print(path)
            with open(os.path.splitext(self.model_path)[0] + ".json", "rt") as f:
                return json.load(f)
        except Exception:
            print("Warning: Could not load y_stats from model file")
            return None

    def predict(self, image_path):
        try:
            pil_image = Image.open(image_path)
            image = self.preprocess(images=pil_image, return_tensors="pt")["pixel_values"].to(self.device)
        except Exception as e:
            print(f"Error processing {image_path}: {e}")
            return None

        with torch.inference_mode():
            image_features = self.clip_model.get_image_features(image)
            im_emb_arr = image_features.type(torch.float)
            prediction = self.mlp_model(im_emb_arr)
        
        if self.y_stats is None or self.raw:
            score = prediction.item()
        else:
            score = prediction.item() * float(self.y_stats["std"]) + float(self.y_stats["mean"])

        return score

    def close(self):
        # Free up resources
        del self.clip_model
        del self.mlp_model
        torch.cuda.empty_cache()

# Example usage
if __name__ == "__main__":
    predictor = AestheticPredictor(model_path='path/to/model', clip_model_name='openai/clip-vit-large-patch14', raw=True)
    score = predictor.predict('path/to/image.jpg')
    print(f"Predicted Score: {score}")
    predictor.close()
