import json
import math
import os
from PIL import Image
import torch
from torchvision.transforms import transforms

class _8305_Tagger:
    def __init__(self):
        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        self.model = None
        self.transform = transforms.Compose([
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ])
        self.tags = None
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True
        torch.autograd.set_detect_anomaly(False)
        torch.autograd.profiler.emit_nvtx(enabled=False)
        torch.autograd.profiler.profile(enabled=False)
        torch.backends.cudnn.benchmark = True

    def load(self, model_path, tags_path):
        self.model = torch.load(model_path)
        self.model.to(self.device)
        self.model.eval()

        with open(tags_path, "r") as f:
            self.tags = json.load(f)
            self.tags.append("placeholder0")
            self.tags = sorted(self.tags)

    def predict_image(self, image_path):
        with torch.no_grad():
            img = Image.open(image_path).convert('RGB')
            aspect_ratio = img.width / img.height
            new_height = math.sqrt(512 ** 2 / aspect_ratio)
            new_width = aspect_ratio * new_height
            img.thumbnail((int(new_width), int(new_height)), Image.LANCZOS)
            tensor = self.transform(img).unsqueeze(0).to(self.device)

            with torch.autocast(device_type='cuda', dtype=torch.bfloat16):
                out = self.model(tensor)
                probabilities = torch.nn.functional.sigmoid(out[0]).to('cpu')

            indices = torch.where(probabilities > 0.3)[0]
            output_tags = [self.tags[idx] for idx in indices if self.tags[idx] != "placeholder0"]
            return output_tags

    def close(self):
        if self.model:
            del self.model
        torch.cuda.empty_cache()

# Usage example
# tagger = _8305_Tagger()
# tagger.load("path_to_model.pth", "path_to_tags.json")
# tags = tagger.predict_image("path_to_image.jpg")
# print(tags)
# tagger.close()
