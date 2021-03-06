import subprocess, sys, os
from core import Submission

sys.stdout = open(os.devnull, 'w')  # do NOT remove this code, place logic & imports below this line

import pandas as pd
import numpy as np
import pickle

import xgboost as xgb
from sklearn.externals import joblib
from sklearn.impute import SimpleImputer
from sklearn.decomposition import PCA
from scipy import stats, linalg
import datetime

import dct_adder


# class MySubmission is the class that you will need to implement
class MySubmission(Submission):

    def __init__(self):
        # Read data transformation parameters
        self.mean_imputer_ = joblib.load('./data_transform_options/mean_imputer.pkl')
        self.zero_imputer_ = SimpleImputer(missing_values=np.nan, strategy='constant', fill_value=0)

        # Define data columns
        self.rate_thresh_list_ = [1, 2, 3, 4]
        self.perc_thresh_list_ = [0.1, 0.2, 0.4]
        self.src_columns_ = np.array(pickle.load(open('./data_transform_options/data_columns.pkl', 'rb')))[:-1]
        self.src_columns_ = np.append(self.src_columns_, ['ratePCA1', 'ratePCA2'])
        for rate_thresh in self.rate_thresh_list_:
            self.src_columns_ = np.append(self.src_columns_, \
                                          ['askCummVolPerc{}'.format(rate_thresh),
                                           'bidCummVolPerc{}'.format(rate_thresh), \
                                           'totalCummVolPerc{}'.format(rate_thresh),
                                           'deltaCummVol{}'.format(rate_thresh)])
        for perc_thresh in self.perc_thresh_list_:
            self.src_columns_ = np.append(self.src_columns_, \
                                          ['askRateMax{}'.format(perc_thresh), 'bidRateMin{}'.format(perc_thresh),
                                           'bidAskSpread{}'.format(perc_thresh)])
        self.src_columns_ = np.append(self.src_columns_, ['askSizeSlope', 'bidSizeSlope', 'bidAskSlopeDiff'])
        self.src_columns_ = np.append(self.src_columns_, ['spreadAskBid0'])
        self.src_columns_ = np.append(self.src_columns_, ['imbalance{}'.format(i) for i in range(15)])
        self.src_columns_ = np.append(self.src_columns_, ['bidSize1_14', 'askSize1_14'])
        self.src_columns_ = np.append(self.src_columns_, ['sizeFlow{}'.format(i) for i in range(1, 9)])
        self.src_columns_ = np.append(self.src_columns_, ['bidAskVolume{}'.format(i) for i in range(6)])

        self.rates_PCA_ = joblib.load('./data_transform_options/rates_PCA.pkl')
        # Historical data accumulation
        self.hist_data_ = np.empty(shape=(0, len(self.src_columns_)))
        self.max_window_ = int(1e3)

        # Core models
        self.xgb1_ = pickle.load(open("./core_models/xgb1_3x_400.pkl", "rb"))
        self.xgb1_columns_ = pickle.load(open("./core_models/xgb1_columns.pkl", "rb"))
        super().__init__()

    """
    get_prediction(data) expects a row of data from data.csv as input and should return a float that represents a
       prediction for the supplied row of data
    """

    def get_prediction(self, data):
        xgb_pred = self.xgb1_.predict(data[self.xgb1_columns_])
        # xgb_h1_pred = self.xgb1_h1_.predict(data[self.xgb1_columns_])
        # xgb_h2_pred = self.xgb1_h2_.predict(data[self.xgb1_columns_])
        #
        # regr_pred = self.regr1_.predict(data[self.regr1_columns_])
        # regr_h1_pred = self.regr1_h1_.predict(data[self.regr1_columns_])
        # regr_h2_pred = self.regr1_h2_.predict(data[self.regr1_columns_])
        #
        # svr_pred = self.svr1_.predict(pd.DataFrame({
        #     'xgb_pred': xgb_pred,
        #     'xgb_h1_pred': xgb_h1_pred,
        #     'xgb_h2_pred': xgb_h2_pred,
        #     'regr_full_pred': regr_pred,
        #     'regr_h1_pred': regr_h1_pred,
        #     'regr_h2_pred': regr_h2_pred
        # }))
        return float(xgb_pred)

    """
    run_submission() will iteratively fetch the next row of data in the format 
       specified (get_next_data_as_string, get_next_data_as_list, get_next_data_as_numpy_array)
       for every prediction submitted to self.submit_prediction()
    """

    def run_submission(self):

        self.debug_print(
            "Use the print function `self.debug_print(...)` for debugging purposes, do NOT use the default `print(...)`")

        # Define bit mask corresponding to position of frequently used groups of values
        pca_cols = ['PCA' in col for col in self.src_columns_]
        volume_cols = ['Volume' in col for col in self.src_columns_]
        imbalance_cols = ['imbalance' in col for col in self.src_columns_]
        ask_rate_cols = [col in ['askRate{}'.format(i) for i in range(15)] for col in self.src_columns_]
        bid_rate_cols = [col in ['bidRate{}'.format(i) for i in range(15)] for col in self.src_columns_]
        rate_cols = np.logical_or(ask_rate_cols, bid_rate_cols)
        ask_size_cols = [col in ['askSize{}'.format(i) for i in range(15)] for col in self.src_columns_]
        bid_size_cols = [col in ['bidSize{}'.format(i) for i in range(15)] for col in self.src_columns_]
        bid_size_1_14_cols = [col in ['bidSize{}'.format(i) for i in range(1, 15)] for col in self.src_columns_]
        ask_size_1_14_cols = [col in ['askSize{}'.format(i) for i in range(1, 15)] for col in self.src_columns_]
        kalman_proc_cols = np.where(self.src_columns_== 'bidSize1_14')[0].tolist() + np.where(self.src_columns_ == 'bidSize0')[0].tolist() + \
                           np.where(self.src_columns_ == 'askSize0')[0].tolist() + np.where(self.src_columns_ == 'askSize1_14')[0].tolist()
        kalman_flow_cols = [col in ['sizeFlow{}'.format(flow) for flow in range(1, 9)] for col in self.src_columns_]
        size_cols = np.logical_or(ask_size_cols, bid_size_cols)

        dct_cols = [
            col not in ['askRate{}'.format(i) for i in range(1, 15)] + ['bidRate{}'.format(i) for i in range(1, 15)] + ['sizeFlow{}'.format(flow) for flow in range(1, 8)] + ['bidSize1_14', 'askSize1_14']
            for col in self.src_columns_]

        # Run DCT
        _dct_adder_100_02 = dct_adder.DCTAttributeAdder(window_size=100, n_coef_perc=0.02, \
                                                        columns=self.src_columns_[dct_cols])
        _dct_adder_1e3_005 = dct_adder.DCTAttributeAdder(window_size=int(1e3), n_coef_perc=0.005, \
                                                         columns=self.src_columns_[dct_cols])
        _dct_adder_1e3_01 = dct_adder.DCTAttributeAdder(window_size=int(1e3), n_coef_perc=0.01, \
                                                         columns=self.src_columns_[dct_cols])
        pred_data = 0

        # Parameters for Kalman filter
        N_states = 12                       # number of states
        N_values = 4                        # number of observable process values
        xhat = np.zeros((N_states, 1))      # a posteri estimate of x
        P = np.identity(N_states)           # a posteriori error estimate
        xhatminus = np.zeros((N_states, 1))  # a priori estimate of x
        Pminus = np.identity(N_states)      # a priori error estimate
        K = np.zeros((N_states, N_values))  # gain or blending factor
        Q = np.identity(N_states) * 1e-3    # process variance
        R = 1                               # estimate of measurement variance
        I_nstates = np.identity(N_states)

        A = np.identity(N_states)
        A[0:4, 4:12] = [
            [1, 0, 0, 0, 0, -1, 0, -1],
            [0, 1, 0, 0, 0, 1, -1, 0],
            [0, 0, 1, 0, 1, 0, 1, 0],
            [0, 0, 0, 1, -1, 0, 0, 1]
        ]
        A_t = np.transpose(A)

        H = np.zeros((N_values, N_states))
        H[0:N_values, 0:N_values] = np.identity(N_values)
        H_t = np.transpose(H)

        i = 0
        start_time = datetime.datetime.now()
        while True:
            i += 1
            if i % 5000 == 0:
                self.debug_print('Processed {} rows, time {}, elapsed {}'.format(i, datetime.datetime.now(),
                                                                                 datetime.datetime.now() - start_time))
                start_time = datetime.datetime.now()

            data = self.get_next_data_as_numpy_array()

            data = np.append(data, np.zeros(self.src_columns_.shape[0] - data.shape[0]), axis=0)

            data[size_cols] = self.zero_imputer_.fit_transform(data[None, size_cols])[0, :]
            data[rate_cols] = self.mean_imputer_.transform(data[None, rate_cols])[0, :]
            data[pca_cols] = self.rates_PCA_.transform(data[None, rate_cols])[0, :]

            # Create additional order book variables
            ask_rate = data[ask_rate_cols]
            bid_rate = data[bid_rate_cols]
            ask_size = data[ask_size_cols]
            bid_size = data[bid_size_cols]
            ask_vol = ask_rate * ask_size
            bid_vol = bid_rate * bid_size
            ask_vol_cumm = np.cumsum(ask_vol)
            bid_vol_cumm = np.cumsum(bid_vol)
            ask_size_cumm = np.cumsum(ask_size)
            bid_size_cumm = np.cumsum(bid_size)

            # % comulative sum +/- X$ rate - bid and ask
            bid_rate_diff = bid_rate[0, None] - bid_rate
            ask_rate_diff = ask_rate - ask_rate[0, None]

            for rate_thresh in self.rate_thresh_list_:
                ask_vol_cumm_flt = ask_vol_cumm * (ask_rate_diff <= rate_thresh)
                bid_vol_cumm_flt = bid_vol_cumm * (bid_rate_diff <= rate_thresh)

                data[self.src_columns_ == 'askCummVolPerc{}'.format(rate_thresh)] = np.max(ask_vol_cumm_flt) / \
                                                                                    ask_vol_cumm[14]
                data[self.src_columns_ == 'bidCummVolPerc{}'.format(rate_thresh)] = np.max(bid_vol_cumm_flt) / \
                                                                                    bid_vol_cumm[14]
                data[self.src_columns_ == 'totalCummVolPerc{}'.format(rate_thresh)] = (np.max(
                    ask_vol_cumm_flt) + np.max(bid_vol_cumm_flt)) / (bid_vol_cumm[14] + ask_vol_cumm[14])
                data[self.src_columns_ == 'deltaCummVol{}'.format(rate_thresh)] = np.max(ask_vol_cumm_flt) - np.max(
                    bid_vol_cumm_flt)

            # Spread of rates under X% of cumm volume - bid and ask
            ask_vol_cumm_perc = ask_vol_cumm / ask_vol_cumm[14, None]
            bid_vol_cumm_perc = bid_vol_cumm / bid_vol_cumm[14, None]
            for perc_thresh in self.perc_thresh_list_:
                data[self.src_columns_ == 'askRateMax{}'.format(perc_thresh)] = np.min(
                    ask_rate * (ask_vol_cumm_perc > perc_thresh) + 1e6 * (ask_vol_cumm_perc <= perc_thresh))
                data[self.src_columns_ == 'bidRateMin{}'.format(perc_thresh)] = np.max(
                    bid_rate * (bid_vol_cumm_perc > perc_thresh))
                data[self.src_columns_ == 'bidAskSpread{}'.format(perc_thresh)] = data[
                                                                                      self.src_columns_ == 'askRateMax{}'.format(
                                                                                          perc_thresh)] - data[
                                                                                      self.src_columns_ == 'bidRateMin{}'.format(
                                                                                          perc_thresh)]

            # Slope of cumm Size vs. Bid and Ask Rate
            ask_size_cumm_perc = ask_size_cumm / ask_size_cumm[14, None]
            bid_size_cumm_perc = bid_size_cumm / bid_size_cumm[14, None]
            data[self.src_columns_ == 'askSizeSlope'] = stats.linregress(ask_rate, ask_size_cumm_perc).slope
            data[self.src_columns_ == 'bidSizeSlope'] = stats.linregress(bid_rate, bid_size_cumm_perc).slope
            data[self.src_columns_ == 'bidAskSlopeDiff'] = data[self.src_columns_ == 'askSizeSlope'] + data[
                self.src_columns_ == 'bidSizeSlope']

            data[self.src_columns_ == 'spreadAskBid0'] = ask_rate[0] - bid_rate[0]

            data[imbalance_cols] = (ask_size_cumm - bid_size_cumm) / (ask_size_cumm + bid_size_cumm + 1)
            data[volume_cols] = (bid_vol - ask_vol)[:sum(volume_cols)]

            # Kalman filter update
            data[self.src_columns_ == 'bidSize1_14'] = np.sum(data[bid_size_1_14_cols])
            data[self.src_columns_ == 'askSize1_14'] = np.sum(data[ask_size_1_14_cols])
            process_data = data[kalman_proc_cols].reshape(-1, 1)

            if i == 1:
                xhat[0:4, 0] = data[kalman_proc_cols]
            else:
                # time update
                xhatminus = A @ xhat
                Pminus = A @ P @ A_t + Q

                # measurement update
                K = Pminus @ H_t @ linalg.inv(H @ Pminus @ H_t + R)
                xhat = xhatminus + (K @ (process_data - H @ xhatminus))
                P = (I_nstates - K @ H) @ Pminus

            data[kalman_flow_cols] = xhat[[flow + 3 for flow in range(1, 9)], 0]

            self.hist_data_ = np.append(self.hist_data_[-self.max_window_:, :],
                                        np.resize(data, (1, self.hist_data_.shape[1])), axis=0)
            prev_pred_data = pred_data
            pred_data = pd.concat([pd.DataFrame(data.reshape(1, -1), columns=self.src_columns_),
                                   _dct_adder_100_02.dct(self.hist_data_[:, dct_cols]),
                                   _dct_adder_1e3_005.dct(self.hist_data_[:, dct_cols]),
                                   _dct_adder_1e3_01.dct(self.hist_data_[:, dct_cols])
                                   ], axis=1)

            if i == 1:
                pred_data_lag = pd.concat([pred_data, \
                                           pd.DataFrame(np.zeros((1, pred_data.shape[1])),
                                                        columns=['{}_delta1'.format(pred) for pred in
                                                                 pred_data.columns])], axis=1)
                pd_colnames = pred_data_lag.columns
                pd_array = np.zeros((1, pred_data_lag.shape[1]))
            else:
                pd_array[0, :pred_data.shape[1]] = pred_data.values
                pd_array[0, pred_data.shape[1]:2 * pred_data.shape[1]] = pd_array[0,
                                                                         :pred_data.shape[1]] - prev_pred_data.values
                pred_data_lag = pd.DataFrame(pd_array, columns=pd_colnames)

            prediction = self.get_prediction(pred_data_lag)

            self.submit_prediction(prediction)

            # DEBUG...
            # if len(self.hist_data_) == 1:
            #     pred_data_lag.to_csv('dct_results.csv', index=False, header=True, mode='a')
            # else:
            #     pred_data_lag.to_csv('dct_results.csv', index=False, header=False, mode='a')


if __name__ == "__main__":
    MySubmission()
