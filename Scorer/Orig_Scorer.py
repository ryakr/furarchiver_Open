import torch
import clip
from PIL import Image
import numpy as np
import pytorch_lightning as pl
import torch.nn as nn


class AestheticScorePredictor:
    def __init__(self, model_path, clip_model_name="ViT-L/14", device="cuda"):
        self.model_path = model_path
        self.clip_model_name = clip_model_name
        self.device = device if torch.cuda.is_available() else "cpu"
        self.model = None
        self.clip_model = None
        self.preprocess = None

    class MLP(pl.LightningModule):
        def __init__(self, input_size):
            super().__init__()
            # Define the layers
            self.layers = nn.Sequential(
                nn.Linear(input_size, 1024),
                nn.Dropout(0.2),
                nn.Linear(1024, 128),
                nn.Dropout(0.2),
                nn.Linear(128, 64),
                nn.Dropout(0.1),
                nn.Linear(64, 16),
                nn.Linear(16, 1)
            )

        def forward(self, x):
            return self.layers(x)

    def load_model(self):
        self.model = self.MLP(768)  # CLIP embedding dim is 768 for CLIP ViT L 14
        print("Set MLP 768")
        self.model.load_state_dict(torch.load(self.model_path))
        print("Loaded state dict")
        self.model.to(self.device)
        self.model.eval()
        self.clip_model, self.preprocess = clip.load(self.clip_model_name, device=self.device)
        print("Loaded CLIP model")

    def unload_model(self):
        del self.model
        del self.clip_model
        torch.cuda.empty_cache()

    def normalized(self, a, axis=-1, order=2):
        l2 = np.atleast_1d(np.linalg.norm(a, order, axis))
        l2[l2 == 0] = 1
        return a / np.expand_dims(l2, axis)

    def predict(self, img_path):
        if not self.model or not self.clip_model:
            raise RuntimeError("Model is not loaded. Please call load_model() first.")

        pil_image = Image.open(img_path)
        image = self.preprocess(pil_image).unsqueeze(0).to(self.device)
        with torch.no_grad():
            image_features = self.clip_model.encode_image(image)
        im_emb_arr = self.normalized(image_features.cpu().detach().numpy())
        prediction = self.model(torch.from_numpy(im_emb_arr).to(self.device).type(torch.cuda.FloatTensor))
        return prediction.item()

# Usage example
# predictor = AestheticScorePredictor("sac+logos+ava1-l14-linearMSE.pth")
# predictor.load_model()
# score = predictor.predict("test.jpg")
# print("Aesthetic score predicted by the model:", score)
# predictor.unload_model()
