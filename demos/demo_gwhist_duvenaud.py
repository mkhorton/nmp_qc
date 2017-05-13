#!/usr/bin/python
# -*- coding: utf-8 -*-

"""
    Trains a Neural Message Passing Model on various datasets. Methodology defined in:

    Gilmer, J., Schoenholz S.S., Riley, P.F., Vinyals, O., Dahl, G.E. (2017)
    Neural Message Passing for Quantum Chemistry.
    arXiv preprint arXiv:1704.01212 [cs.LG]
"""

# Own Modules
import datasets
from datasets import utils
from models.model import NMP_Duvenaud
from LogMetric import AverageMeter, Logger

# Torch
import torch
import torch.optim as optim
import torch.nn as nn
from torch.autograd import Variable

import time
import argparse
import os, sys
import numpy as np

reader_folder = os.path.realpath( os.path.abspath('../GraphReader'))
if reader_folder not in sys.path:
    sys.path.insert(1, reader_folder)

from GraphReader.graph_reader import read_2cols_set_files, create_numeric_classes

__author__ = "Pau Riba, Anjan Dutta"
__email__ = "priba@cvc.uab.cat, adutta@cvc.uab.cat"

torch.multiprocessing.set_sharing_strategy('file_system')

# Parser check
def restricted_float(x, inter):
    x = float(x)
    if x < inter[0] or x > inter[1]:
        raise argparse.ArgumentTypeError("%r not in range [1e-5, 1e-4]"%(x,))
    return x

# Argument parser
parser = argparse.ArgumentParser(description='Neural message passing')

parser.add_argument('--dataset', default='gwhistograph', help='GWHISTOGRAPH')
parser.add_argument('--datasetPath', default='../data/GWHistoGraphs/', help='dataset path')
parser.add_argument('--subSet', default='01_Keypoint', help='sub dataset')
parser.add_argument('--logPath', default='../log/', help='log path')
# Optimization Options
parser.add_argument('--batch-size', type=int, default=20, metavar='N',
                    help='Input batch size for training (default: 20)')
parser.add_argument('--no-cuda', action='store_true', default=False,
                    help='Enables CUDA training')
parser.add_argument('--epochs', type=int, default=360, metavar='N',
                    help='Number of epochs to train (default: 360)')
parser.add_argument('--lr', type=lambda x: restricted_float(x, [1e-5, 0.5]), default=0.001, metavar='LR',
                    help='Initial learning rate [1e-5, 5e-4] (default: 1e-4)')
parser.add_argument('--lr-decay', type=lambda x: restricted_float(x, [.01, 1]), default=0.6, metavar='LR-DECAY',
                    help='Learning rate decay factor [.01, 1] (default: 0.6)')
parser.add_argument('--schedule', type=list, default=[0.1, 0.9], metavar='S',
                    help='Percentage of epochs to start the learning rate decay [0, 1] (default: [0.1, 0.9])')
parser.add_argument('--momentum', type=float, default=0.9, metavar='M',
                    help='SGD momentum (default: 0.9)')
# i/o
parser.add_argument('--log-interval', type=int, default=100, metavar='N',
                    help='How many batches to wait before logging training status')
# Accelerating
parser.add_argument('--prefetch', type=int, default=2, help='Pre-fetching threads.')


def main():
    global args
    args = parser.parse_args()

    # Check if CUDA is enabled
    args.cuda = not args.no_cuda and torch.cuda.is_available()

    # Load data
    root = args.datasetPath
    subset = args.subSet

    print('Prepare files')
    
    train_classes, train_ids = read_2cols_set_files(os.path.join(root, 'Set/Train.txt'))
    valid_classes, valid_ids = read_2cols_set_files(os.path.join(root, 'Set/Valid.txt'))
    test_classes, test_ids = read_2cols_set_files(os.path.join(root,'Set/Test.txt'))
    
    train_classes, valid_classes, test_classes = create_numeric_classes(train_classes, valid_classes, test_classes)

    train_classes = train_classes + valid_classes
    train_ids = train_ids + valid_ids

    del valid_classes, valid_ids
    
    num_classes = max(train_classes + test_classes) + 1
    data_train = datasets.GWHISTOGRAPH(root, subset, train_ids, train_classes, num_classes)
    data_test = datasets.GWHISTOGRAPH(root, subset, test_ids, test_classes, num_classes)
    
    # Define model and optimizer
    print('Define model')
    # Select one graph
    g_tuple, l = data_train[0]
    g, h_t, e = g_tuple
    
    print('\tStatistics')
    stat_dict = datasets.utils.get_graph_stats(data_train, ['degrees'])

    # Data Loader
    train_loader = torch.utils.data.DataLoader(data_train,
                                               batch_size=args.batch_size, shuffle=True, collate_fn=datasets.utils.collate_g,
                                               num_workers=args.prefetch, pin_memory=True)
    test_loader = torch.utils.data.DataLoader(data_test,
                                              batch_size=args.batch_size, shuffle=False, collate_fn=datasets.utils.collate_g,
                                              num_workers=args.prefetch, pin_memory=True)

    print('\tCreate model')
    model = NMP_Duvenaud(stat_dict['degrees'], [len(h_t[0]), len(list(e.values())[0])], [5, 15, 15], 30, num_classes, type='classification')

    print('Check cuda')
    if args.cuda:
        model.cuda()

    print('Optimizer')
    optimizer = optim.Adam(model.parameters(), lr=args.lr)

    criterion = nn.CrossEntropyLoss()
    if args.cuda:
        criterion = criterion.cuda()

    evaluation = utils.accuracy

    print('Logger')
    logger = Logger(args.logPath)

    lr_step = (args.lr-args.lr*args.lr_decay)/(args.epochs*args.schedule[1] - args.epochs*args.schedule[0])

    # Epoch for loop
    for epoch in range(0, args.epochs):

        if epoch > args.epochs*args.schedule[0] and epoch < args.epochs*args.schedule[1]:
            args.lr -= lr_step
            for param_group in optimizer.param_groups:
                param_group['lr'] = args.lr

        # train for one epoch
        train(train_loader, model, criterion, optimizer, epoch, evaluation, logger)

        # evaluate on test set
        validate(test_loader, model, criterion, evaluation, logger)

        # Logger step
        logger.log_value('learning_rate', args.lr).step()

def train(train_loader, model, criterion, optimizer, epoch, evaluation, logger):
    batch_time = AverageMeter()
    data_time = AverageMeter()
    losses = AverageMeter()
    accuracies = AverageMeter()

    # switch to train mode
    model.train()

    end = time.time()
    for i, (g, h, e, target) in enumerate(train_loader):
        
        # Prepare input data
        if args.cuda:
            g, h, e, target = g.cuda(), h.cuda(), e.cuda(), target.cuda()
        g, h, e, target = Variable(g), Variable(h), Variable(e), Variable(target)

        # Measure data loading time
        data_time.update(time.time() - end)

        def closure():
            optimizer.zero_grad()

            # Compute output
            output = model(g, h, e)
            train_loss = criterion(output, torch.squeeze(target.type(torch.cuda.LongTensor)))
            # compute gradient and do SGD step
            train_loss.backward()
            return train_loss

        optimizer.step(closure)


        output = model(g, h, e)
        train_loss = criterion(output, torch.squeeze(target.type(torch.cuda.LongTensor)))
        acc = Variable(evaluation(output.data, target, topk=(1,))[0])

        # Logs
        losses.update(train_loss.data[0], g.size(0))
        accuracies.update(acc.data[0], g.size(0))

        # Measure elapsed time
        batch_time.update(time.time() - end)
        end = time.time()

        if i % args.log_interval == 0:
            
            print('Epoch: [{0}][{1}/{2}]\t'
                  'Time {batch_time.val:.3f} ({batch_time.avg:.3f})\t'
                  'Data {data_time.val:.3f} ({data_time.avg:.3f})\t'
                  'Loss {loss.val:.4f} ({loss.avg:.4f})\t'
                  'Accuracy {acc.val:.4f} ({acc.avg:.4f})'
                  .format(epoch, i, len(train_loader), batch_time=batch_time,
                          data_time=data_time, loss=losses, acc=accuracies))
                          
    logger.log_value('train_epoch_loss', losses.avg)
    logger.log_value('train_epoch_accuracy', accuracies.avg)


def validate(val_loader, model, criterion, evaluation, logger):
    batch_time = AverageMeter()
    losses = AverageMeter()
    accuracies = AverageMeter()

    # switch to evaluate mode
    model.eval()

    end = time.time()
    for i, (g, h, e, target) in enumerate(val_loader):

        # Prepare input data
        if args.cuda:
            g, h, e, target = g.cuda(), h.cuda(), e.cuda(), target.cuda()
        g, h, e, target = Variable(g), Variable(h), Variable(e), Variable(target)

        # Compute output
        output = model(g, h, e)

        # Logs
        losses.update(criterion(output, torch.squeeze(target.type(torch.cuda.LongTensor))).data[0])
        acc = Variable(evaluation(output.data, target, topk=(1,))[0])
        accuracies.update(acc.data[0])

        # measure elapsed time
        batch_time.update(time.time() - end)
        end = time.time()

        if i % args.log_interval == 0:
            
            print('Test: [{0}/{1}]\t'
                  'Time {batch_time.val:.3f} ({batch_time.avg:.3f})\t'
                  'Loss {loss.val:.4f} ({loss.avg:.4f})\t'
                  'Accuracy {acc.val:.4f} ({acc.avg:.4f})'
                  .format(i, len(val_loader), batch_time=batch_time,
                          loss=losses, acc=accuracies))

    print(' * Average Accuracy {acc.avg:.3f}'
          .format(acc=accuracies))
          
    logger.log_value('test_epoch_loss', losses.avg)
    logger.log_value('test_epoch_accuracy', accuracies.avg)
    
if __name__ == '__main__':
    main()
