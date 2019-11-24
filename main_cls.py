import os
import time

import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
import argparse

from modeling.classification.MobileNetV2 import mobilenet_v2
from torch.utils.data import DataLoader
from torchvision import transforms, datasets

from utils.relation import create_relation
from dfq import cross_layer_equalization, bias_absorption, bias_correction
from utils.layer_transform import switch_layers, replace_op, restore_op, set_quant_minmax, merge_batchnorm#, LayerTransform
from PyTransformer.transformers.torchTransformer import TorchTransformer

from PyTransformer.transformers.quantize import QuantConv2d, QuantLinear

def get_argument():
    parser = argparse.ArgumentParser()
    parser.add_argument("--quantize", action='store_true')
    parser.add_argument("--equalize", action='store_true')
    parser.add_argument("--relu", action='store_true')
    return parser.parse_args()


def inference_all(model):
    print("Start inference")
    imagenet_dataset = datasets.ImageFolder('D:/workspace/dataset/ILSVRC/Data/CLS-LOC/val', transforms.Compose([
        transforms.Resize(256),
        transforms.CenterCrop(224),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406],
                                 std=[0.229, 0.224, 0.225]),
    ]))

    dataloader = DataLoader(imagenet_dataset, batch_size=256, shuffle=False, num_workers=0, pin_memory=True)

    num_correct = 0
    num_total = 0
    with torch.no_grad():
        for ii, sample in enumerate(dataloader):
            image, label = sample[0].cuda(), sample[1].numpy()
            logits = model(image)

            pred = torch.max(logits, 1)[1].cpu().numpy()
            
            num_correct += np.sum(pred == label)
            num_total += image.shape[0]
            # print(num_correct, num_total, num_correct/num_total)

    print("Acc: {}".format(num_correct / num_total))


def main():
    args = get_argument()
    data = torch.ones((4, 3, 224, 224))#.cuda()

    model = mobilenet_v2('modeling/classification/mobilenetv2_1.0-f2a8633.pth.tar')
    model.eval()
    
    transformer = TorchTransformer()
    module_dict = {}
    if args.quantize:
        module_dict[1] = [(nn.Conv2d, QuantConv2d), (nn.Linear, QuantLinear)]
    
    if args.relu:
        module_dict[0] = [(torch.nn.ReLU6, torch.nn.ReLU)]

    model = switch_layers(model, transformer, data, module_dict, quant_op=args.quantize)

    # use cpu to process
    transformer = TorchTransformer()
    model = model.cpu()
    data = torch.ones((4, 3, 224, 224))#.cuda()
    # transformer.summary(model, data)
    # transformer.visualize(model, data, 'graph_cls', graph_size=120)

    transformer._build_graph(model, data) # construt graph after all state_dict loaded

    graph = transformer.log.getGraph()
    bottoms = transformer.log.getBottoms()
    output_shape = transformer.log.getOutShapes()
    if args.quantize:
        targ_layer = [QuantConv2d, QuantLinear]
    else:
        targ_layer = [nn.Conv2d, nn.Linear]

    model = merge_batchnorm(model, graph, bottoms, targ_layer)

    #create relations
    if args.equalize:
        res = create_relation(graph, bottoms, targ_layer)
        cross_layer_equalization(graph, res, visualize_state=False, converge_thres=1e-9)

    # bias_absorption(graph, res, bottoms, 3)
    # bias_correction(graph, bottoms, [QuantConv2d, QuantLinear])

    if args.quantize:
        set_quant_minmax(graph, bottoms, output_shape)
    
    model = model.cuda()
    model.eval()

    if args.quantize:
        replace_op()
    inference_all(model)
    if args.quantize:
        restore_op()


if __name__ == '__main__':
    main()