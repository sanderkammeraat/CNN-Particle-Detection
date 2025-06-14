# Class to load the dataset.
from torch import nn
import torch
import cv2
import numpy as np

class ParticleDataset():
    '''
    Class: To read the images and pass it into the dataloader for pytorch.
    Since the images are sequences of moving particles, we shuffle the frames 
    to prevent the NN from learning the physics of the trajectories and purely focus
    the features and overlap within each image.
    
    ** NOTE: The shuffle is done on the index to maintain the pair of image and its 
             corresponding Heatmap.    
    
    Params: 
        Dataset: takes 1. the image directory path 2. the heatmap directory  
        
    Returns: 
        img: (shuffled sequence)
        heatmap: (the corresponding heatmap of the image)
    
    '''
    
    def __init__(self, img_paths_, heatmap_paths_):
        self.img_paths = img_paths_
        self.heatmap_paths = heatmap_paths_

    def __len__(self):
        return len(self.img_paths)

    def __getitem__(self, idx):
        
        # Load image and pass to torch tensor
        img = cv2.imread(self.img_paths[idx], cv2.IMREAD_GRAYSCALE) / 255.0 # normalise it 
        img = torch.tensor(img, dtype=torch.float32).unsqueeze(0)  # (1, H, W)

        # Load heatmap and pass to torch tensor
        heatmap = np.load(self.heatmap_paths[idx], allow_pickle=True)
        heatmap = torch.tensor(heatmap, dtype=torch.float32).unsqueeze(0)  # (1, H, W)

        return img, heatmap
    

    
# define the UNet Model
class UNetHeatmap(nn.Module):
    def __init__(self, out_channels=1):
        super().__init__()

        # Encoder 
        
        # ... layer 1 with 2 conv and 1 maxpool 
        self.enc1 = nn.Sequential(
            nn.Conv2d(1, 32, kernel_size=3, padding=1), nn.ReLU(),
            nn.Conv2d(32, 32, kernel_size=3, padding=1), nn.ReLU()
        )
        self.pool1 = nn.MaxPool2d(2)
        
        # ... layer 2 with 2 conv and 1 maxpool 
        self.enc2 = nn.Sequential(
            nn.Conv2d(32, 64, kernel_size=3, padding=1), nn.ReLU(),
            nn.Conv2d(64, 64, kernel_size=3, padding=1), nn.ReLU()
        )
        self.pool2 = nn.MaxPool2d(2)

        # Bottleneck  shifting from 64 to 128 
        self.bottleneck = nn.Sequential(
            nn.Conv2d(64, 128, kernel_size=3, padding=1), nn.ReLU(),
            nn.Conv2d(128, 128, kernel_size=3, padding=1), nn.ReLU()
        )

        # Decoder
        # ... layer 3 with 2 conv
        self.upconv2 = nn.ConvTranspose2d(128, 64, kernel_size=2, stride=2)
        self.dec2 = nn.Sequential(
            nn.Conv2d(128, 64, kernel_size=3, padding=1), nn.ReLU(),
            nn.Conv2d(64, 64, kernel_size=3, padding=1), nn.ReLU()
        )
        
        # ... layer 4 with 1 upconv and 2 conv  
        self.upconv1 = nn.ConvTranspose2d(64, 32, kernel_size=2, stride=2)
        self.dec1 = nn.Sequential(
            nn.Conv2d(64, 32, kernel_size=3, padding=1), nn.ReLU(),
            nn.Conv2d(32, 32, kernel_size=3, padding=1), nn.ReLU()
        )

        # ===== Output Layer =====
        self.final_conv = nn.Conv2d(32, out_channels, kernel_size=1)

    def forward(self, x):
        # look at the comments carefully to see the dimension flow of the entire structure 
        #[batch, filters, height, width] 
        # Encoder path 
        enc1 = self.enc1(x)         # [B, 32, H, W]
        x = self.pool1(enc1)        # [B, 32, H/2, W/2]

        enc2 = self.enc2(x)         # [B, 64, H/2, W/2]
        x = self.pool2(enc2)        # [B, 64, H/4, W/4]

        # Bottleneck
        x = self.bottleneck(x)      # [B, 128, H/4, W/4]

        # Decoder path
        x = self.upconv2(x)         # [B, 64, H/2, W/2]
        x = torch.cat([x, enc2], dim=1)  # skip connection
        x = self.dec2(x)            # [B, 64, H/2, W/2]

        x = self.upconv1(x)         # [B, 32, H, W]
        x = torch.cat([x, enc1], dim=1)  # skip connection
        x = self.dec1(x)            # [B, 32, H, W]

        x = self.final_conv(x)      # [B, out_channels, H, W]
        return x # do not use a sigmoid here ... we will loose information of subpixel accuracy.
    
# define a ResNet model 
# ..... this is still on the experimental side...... is larger but shows a similar performance as the 

# Build a residual block 
class ResidualBlock(nn.Module):
    def __init__(self, in_channels, out_channels, downsample=False):
        super().__init__()
        stride = 2 if downsample else 1

        self.conv1 = nn.Conv2d(in_channels, out_channels, kernel_size=3, stride=stride, padding=1)
        self.bn1 = nn.BatchNorm2d(out_channels)
        self.relu = nn.ReLU(inplace=True)

        self.conv2 = nn.Conv2d(out_channels, out_channels, kernel_size=3, padding=1)
        self.bn2 = nn.BatchNorm2d(out_channels)

        self.skip = nn.Sequential()
        if downsample or in_channels != out_channels:
            self.skip = nn.Sequential(
                nn.Conv2d(in_channels, out_channels, kernel_size=1, stride=stride),
                nn.BatchNorm2d(out_channels)
            )

    def forward(self, x):
        identity = self.skip(x)
        out = self.relu(self.bn1(self.conv1(x)))
        out = self.bn2(self.conv2(out))
        out += identity
        return self.relu(out)

class ResNetHeatmap(nn.Module):
    def __init__(self, out_channels=1):
        super().__init__()
        self.in_channels = 64

        # Stem
        self.stem = nn.Sequential(
            nn.Conv2d(1, 64, kernel_size=7, stride=2, padding=3),
            nn.BatchNorm2d(64),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=3, stride=2, padding=1)
        )

        # Encoder
        self.layer1 = self._make_layer(64, 2, downsample=False)
        self.layer2 = self._make_layer(128, 2, downsample=True)
        self.layer3 = self._make_layer(256, 2, downsample=True)

        # Decoder (Upsampling layers inline)
        self.up1 = nn.Sequential(
            nn.ConvTranspose2d(256, 128, kernel_size=2, stride=2),
            nn.BatchNorm2d(128),
            nn.ReLU(inplace=True)
        )
        self.up2 = nn.Sequential(
            nn.ConvTranspose2d(128, 64, kernel_size=2, stride=2),
            nn.BatchNorm2d(64),
            nn.ReLU(inplace=True)
        )
        self.up3 = nn.Sequential(
            nn.ConvTranspose2d(64, 32, kernel_size=2, stride=2),
            nn.BatchNorm2d(32),
            nn.ReLU(inplace=True)
        )
        self.up4 = nn.Sequential(
            nn.ConvTranspose2d(32, 16, kernel_size=2, stride=2),
            nn.BatchNorm2d(16),
            nn.ReLU(inplace=True)
        )

        # Final layer (no sigmoid)
        self.out = nn.Conv2d(16, out_channels, kernel_size=1)

    def _make_layer(self, out_channels, blocks, downsample):
        layers = [ResidualBlock(self.in_channels, out_channels, downsample)]
        self.in_channels = out_channels
        for _ in range(1, blocks):
            layers.append(ResidualBlock(out_channels, out_channels))
        return nn.Sequential(*layers)

    def forward(self, x):
        x = self.stem(x)
        x = self.layer1(x)
        x = self.layer2(x)
        x = self.layer3(x)

        x = self.up1(x)
        x = self.up2(x)
        x = self.up3(x)
        x = self.up4(x)

        x = self.out(x)  # raw logits
        return x