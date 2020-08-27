import matplotlib.pyplot as plt
import torch.nn as nn
import torch.nn.functional as F
import torch
import numpy as np
from tqdm import tqdm
from sklearn.cluster import DBSCAN
from sklearn.neighbors import KNeighborsClassifier
from sklearn.decomposition import KernelPCA
import pickle
from util import open_model_json, showTensor
from collections import Counter

class Detector(nn.Module):
    '''
        Detect if the given cluster is in the next frame or not
    '''
    def __init__(self, params):
        '''
            See model_config.json for parameter values
        '''
        super(Detector, self).__init__()
        self.params = params.copy()
        self.predictor = None
        self.classifier = None

    def forward(self, frame1, frame2, target, train=True):
        '''
            1. input_seq -> fetaure_extractor => features
            2. features -> RNN(bidrectional=true) => forwards and backwards predictions
            3. predictions -> loss_calculator => loss
            4. return loss

            Input: input_seq has shape [batch, time_step, depth, z, x, y]
                   target, the object we are trying to track through time
                           it is h_0 

        '''
        # print(frame2.shape, frame1.shape, target.shape)
        # self.graph_3d(frame1)
        # self.graph_3d(frame2)
        # self.graph_3d(target)
        f1 = frame1.view(-1,
                         frame1.size(2),
                         frame1.size(3),
                         frame1.size(4),
                         frame1.size(5))  # (batch * time_frame), D, Z , H, W
        f2 = frame2.view(-1,
                         frame2.size(2),
                         frame2.size(3),
                         frame2.size(4),
                         frame2.size(5))  # (batch * time_frame), D, Z , H, W
        targ = target.view(target.size(3),
                           target.size(4),
                           target.size(5))  # Z , H, W
        # print(f2.shape, f1.shape, targ.shape)

        # (out_channels, in_channels, kZ, kH, kW)
        # 5 filters with same shape as entire frame
        weights = torch.empty(
            (7, 1, target.size(3), target.size(4), target.size(5))).cuda()

        # create custom kernel filters by transforming target
        weights[0, ...] = targ  # original target mask
        weights[1, ...] = torch.roll(targ,
                                     shifts=(0, 3, 0),
                                     dims=(0, 1, 2))  # roll x by 3
        weights[2, ...] = torch.roll(targ,
                                     shifts=(0, -3, 0),
                                     dims=(0, 1, 2))  # roll x by -3
        weights[3, ...] = torch.roll(targ,
                                     shifts=(0, 0, 3),
                                     dims=(0, 1, 2))  # roll y by 3
        weights[4, ...] = torch.roll(targ,
                                     shifts=(0, 0, -3),
                                     dims=(0, 1, 2))  # roll y by -3
        weights[5, ...] = torch.roll(targ,
                                     shifts=(2, 0, 0),
                                     dims=(0, 1, 2))  # roll z by 2
        weights[6, ...] = torch.roll(targ,
                                     shifts=(-2, 0, 0),
                                     dims=(0, 1, 2))  # roll z by -2
        
        if train:
            # only need these feature for fitting the detector
            f1_features = F.conv3d(input=f1, weight=weights)
            f1_features = torch.flatten(f1_features)
        else:
            f1_features = None 
            
        f2_features = F.conv3d(input=f2, weight=weights)
        f2_features = torch.flatten(f2_features)

        return f1_features, f2_features

    def predict(self, feature):
        # print(feature)
        # load the model
        if self.predictor is None or self.classifier is None:
            with open('../../models/detector.pickle', 'rb') as f:
                self.predictor = pickle.load(f)
                tqdm.write('Loaded the trained predictor model')
             

            # init classifier
            self.classifier = KNeighborsClassifier(
                n_neighbors=len(np.unique(self.predictor['DBSCAN'].labels_)))
            self.classifier.fit(self.predictor['DBSCAN'].components_,
                                self.predictor['DBSCAN'].labels_[self.predictor['DBSCAN'].core_sample_indices_])

        pca2 = self.predictor['PCA2']
        p2 = pca2.transform(feature)

        # print(p2)
        predictions = self.classifier.predict(p2)
        return predictions

    def train_feat(self, f1, f2, graph=True):
        # need to reduce the dimesionality of the data
        pca1 = KernelPCA(n_components=2, kernel='sigmoid', gamma=0.7)
        pca2 = KernelPCA(n_components=2, kernel='sigmoid', gamma=0.7)
        t1 = pca1.fit_transform(f1)
        t2 = pca2.fit_transform(f2)

        # data is now clustered f1 data should all be in one cluster
        cluster_data = np.concatenate((t1, t2), axis=0)

        # dbscan will label the clusters
        dbscan = DBSCAN(eps=0.005, min_samples=10)
        dbscan.fit(cluster_data)

        if graph:
            fig = plt.figure()
            ax = fig.add_subplot()
            ax.scatter(t1[:, 0], t1[:, 1], c='r')
            ax.scatter(t2[:, 0], t2[:, 1], marker='x')
            plt.show()
            # print(dbscan.labels_[0:20])
            # print(dbscan.core_sample_indices_[0:20] + 1)
            # print(dbscan.labels_[10000:10020])
            print('Labels: ', Counter(dbscan.labels_).keys())
            print('Label Counts: ', Counter(dbscan.labels_).values())
            self.plot_dbscan(dbscan, cluster_data, size=100)

        # save the models
        self.predictor = {'PCA1': pca1, 'PCA2': pca2, 'DBSCAN': dbscan}
        with open('../../models/detector.pickle', 'wb') as f:
            pickle.dump(self.predictor, f, pickle.HIGHEST_PROTOCOL)

    def plot_dbscan(self, dbscan, X, size, show_xlabels=True, show_ylabels=True):
        core_mask = np.zeros_like(dbscan.labels_, dtype=bool)
        core_mask[dbscan.core_sample_indices_] = True
        anomalies_mask = dbscan.labels_ == -1
        non_core_mask = ~(core_mask | anomalies_mask)

        cores = dbscan.components_
        anomalies = X[anomalies_mask]
        non_cores = X[non_core_mask]

        plt.scatter(cores[:, 0], cores[:, 1],
                    c=dbscan.labels_[core_mask], marker='o', s=size, cmap="Paired")
        plt.scatter(cores[:, 0], cores[:, 1], marker='*',
                    s=20, c=dbscan.labels_[core_mask])
        plt.scatter(anomalies[:, 0], anomalies[:, 1],
                    c="r", marker="x", s=100)
        plt.scatter(non_cores[:, 0], non_cores[:, 1],
                    c=dbscan.labels_[non_core_mask], marker=".")
        if show_xlabels:
            plt.xlabel("$x_1$", fontsize=14)
        else:
            plt.tick_params(labelbottom=False)
        if show_ylabels:
            plt.ylabel("$x_2$", fontsize=14, rotation=0)
        else:
            plt.tick_params(labelleft=False)
        plt.title("eps={:.2f}, min_samples={}".format(
            dbscan.eps, dbscan.min_samples), fontsize=14)
        plt.show()

    def graph_3d(self, f):
        fig = plt.figure()
        ax = fig.add_subplot(111, projection='3d')
        z, x, y = f[0, 0, 0, ...].cpu().numpy().nonzero()
        ax.set_xlim3d(0, 280)
        ax.set_ylim3d(0, 512)
        ax.set_zlim3d(0, 13)
        ax.scatter(x, y, z, zdir='z')
        plt.show()


if __name__ == "__main__":
    # test
    model_config = open_model_json('./model_config.json')
    model = Detector(model_config['default'])
    print(model)
    param_num = sum([param.data.numel()
                     for param in model.parameters()])
    print('Parameter number: %.3f ' % (param_num))
