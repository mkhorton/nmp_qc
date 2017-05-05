#!/usr/bin/python                                                                                                                                                                                               
# -*- coding: utf-8 -*-

"""
    MessageFunction.py: Propagates a message depending on two nodes and their common edge.

    Usage:

"""

from __future__ import print_function

# Own modules
import datasets
from MessageFunction import MessageFunction
from UpdateFunction import UpdateFunction

import time
import torch
import torch.nn as nn
import os
import argparse
import numpy as np


#dtype = torch.cuda.FloatTensor
dtype = torch.FloatTensor

__author__ = "Pau Riba, Anjan Dutta"
__email__ = "priba@cvc.uab.cat, adutta@cvc.uab.cat" 


class ReadoutFunction(nn.Module):

    # Constructor
    def __init__(self, readout_def='nn', args={}):
        super(ReadoutFunction, self).__init__()
        self.r_definition = ''
        self.r_function = None
        self.args = {}
        self.__set_readout(readout_def, args)

    # Readout graph given node values at las layer
    def forward(self, h_v):
        return self.r_function(h_v)

    # Set a readout function
    def __set_readout(self, readout_def, args):
        self.r_definition = readout_def.lower()

        self.r_function = {
                    'duvenaud': self.r_duvenaud
                }.get(self.r_definition, None)

        if self.r_function is None:
            print('WARNING!: Readout Function has not been set correctly\n\tIncorrect definition ' + readout_def)
            quit()

        self.learn_args, self.learn_modules, self.args = {
                    'duvenaud': self.init_duvenaud(args)
                }.get(self.r_definition, (nn.ParameterList([]),nn.ModuleList([]),{}))

    # Get the name of the used readout function
    def get_definition(self):
        return self.r_definition

    ## Definition of various state of the art update functions

    # Duvenaud
    def r_duvenaud(self, h):
        # layers
        aux = []
        for l in range(len(h)):
            for j in range(0,h[l].size()[0]):
                aux.append(torch.squeeze(nn.Softmax()(torch.mv(torch.t(self.learn_args[l]), h[l][j]).view(1, self.args['out']))))

        aux = torch.sum(torch.stack(aux, 0),0)
        return torch.squeeze(self.learn_modules[0](aux))

    def init_duvenaud(self, params):
        learn_args = []
        learn_modules = []
        args = {}

        args['out'] = params['out']

        # Define a parameter matrix W for each layer.
        for l in range(params['layers']):
            learn_args.append(nn.Parameter(torch.randn(params['in'][l], params['out'])))

        learn_modules.append(nn.Linear(params['out'], params['target']))
        return nn.ParameterList(learn_args), nn.ModuleList(learn_modules), args

if __name__ == '__main__':
    # Parse optios for downloading
    parser = argparse.ArgumentParser(description='QM9 Object.')
    # Optional argument
    parser.add_argument('--root', nargs=1, help='Specify the data directory.', default=['./data/qm9/dsgdb9nsd/'])

    args = parser.parse_args()
    root = args.root[0]

    files = [f for f in os.listdir(root) if os.path.isfile(os.path.join(root, f))]

    idx = np.random.permutation(len(files))
    idx = idx.tolist()

    valid_ids = [files[i] for i in idx[0:10000]]
    test_ids = [files[i] for i in idx[10000:20000]]
    train_ids = [files[i] for i in idx[20000:]]

    data_train = datasets.Qm9(root, train_ids)
    data_valid = datasets.Qm9(root, valid_ids)
    data_test = datasets.Qm9(root, test_ids)

    # d = datasets.utils.get_graph_stats(data_train, 'degrees')
    d = [1, 2, 3, 4]

    ## Define message
    m = MessageFunction('duvenaud')

    ## Parameters for the update function
    # Select one graph
    g_tuple, l = data_train[0]
    g, h_t, e = g_tuple

    m_v = m.forward(h_t[0], h_t[1], e[list(e.keys())[0]])

    in_n = len(m_v)
    out_n = 30

    ## Define Update
    u = UpdateFunction('duvenaud', args={'deg': d, 'in': in_n, 'out': out_n})

    in_n = len(h_t[0])

    ## Define Readout
    r = ReadoutFunction('duvenaud', args={'layers': 2, 'in': [in_n, out_n], 'out': 50, 'target': len(l)})

    print(m.get_definition())
    print(u.get_definition())
    print(r.get_definition())

    start = time.time()

    # Layers
    h = []

    # Select one graph
    g_tuple, l = data_train[0]
    g, h_in, e = g_tuple

    h.append(h_in)

    # Layer
    t = 1
    h.append({})
    for v in g.nodes_iter():
        neigh = g.neighbors(v)
        m_neigh = dtype()
        for w in neigh:
            if (v, w) in e:
                e_vw = e[(v, w)]
            else:
                e_vw = e[(w, v)]
            m_v = m.forward(h[t-1][v], h[t-1][w], e_vw)
            if len(m_neigh):
                m_neigh += m_v
            else:
                m_neigh = m_v

        # Duvenaud
        opt = {'deg': len(neigh)}
        h[t][v] = u.forward(h[t-1][v], m_neigh, opt)

    # Readout
    res = r.forward(h)

    end = time.time()


    print(res)
    print('Time')
    print(end - start)
