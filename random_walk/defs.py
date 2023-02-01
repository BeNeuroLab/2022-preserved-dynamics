import pandas as pd
import numpy as np
import sklearn
from sklearn.decomposition import PCA
import scipy.linalg as linalg
import scipy.stats as stats
from sklearn.linear_model import Ridge, LinearRegression
from sklearn.model_selection import cross_val_score
from sklearn.metrics import make_scorer, r2_score
import pyaldata as pyal
import math

rng = np.random.default_rng(np.random.SeedSequence(12345))

# raster_example = ('Chewie_CO_FF_2016-10-13.p', 'Mihili_CO_VR_2014-03-03.p')
# MAX_HISTORY = 3  #int: no of bins to be added as history
BIN_SIZE = .03  # sec
# WINDOW_prep = (-.4, .05)  # sec
WINDOW_exec = (0.0, .35)  # sec
# n_components = 10  # min between M1 and PMd
# areas = ('M1', 'PMd', 'MCx')
n_target_groups = 8
subset_radius = 2.5

# prep_epoch = pyal.generate_epoch_fun(start_point_name='idx_movement_on',
#                                      rel_start=int(WINDOW_prep[0]/BIN_SIZE),
#                                      rel_end=int(WINDOW_prep[1]/BIN_SIZE)
#                                     )
exec_epoch = pyal.generate_epoch_fun(start_point_name='idx_movement_on',
                                     rel_start=int(WINDOW_exec[0]/BIN_SIZE),
                                     rel_end=int(WINDOW_exec[1]/BIN_SIZE)
                                    )
# fixation_epoch = pyal.generate_epoch_fun(start_point_name='idx_target_on',
#                                          rel_start=int(WINDOW_prep[0]/BIN_SIZE),
#                                          rel_end=int(WINDOW_prep[1]/BIN_SIZE)
#                                         )
# exec_epoch_decode = pyal.generate_epoch_fun(start_point_name='idx_movement_on',
#                                      rel_start=int(WINDOW_exec[0]/BIN_SIZE) - MAX_HISTORY,
#                                      rel_end=int(WINDOW_exec[1]/BIN_SIZE)
#                                     )

# def get_target_id(trial):
#     return int(np.round((trial.target_direction + np.pi) / (0.25*np.pi))) - 1

def prep_general (df):
    "preprocessing general!"
    time_signals = [signal for signal in pyal.get_time_varying_fields(df) if 'spikes' in signal]
    # df["target_id"] = df.apply(get_target_id, axis=1)  # add a field `target_id` with int values
    df['session'] = df.monkey[0]+':'+df.date[0]
    
    for signal in time_signals:
        df_ = pyal.remove_low_firing_neurons(df, signal, 1)
    
    MCx_signals = [signal for signal in time_signals if 'M1' in signal or 'PMd' in signal]
    if len(MCx_signals) > 1:
        df_ = pyal.merge_signals(df_, MCx_signals, 'MCx_spikes')
    elif len(MCx_signals) == 1:
        df_ = pyal.rename_fields(df_, {MCx_signals[0]:'MCx_spikes'})
    time_signals = [signal for signal in pyal.get_time_varying_fields(df_) if 'spikes' in signal]

    df_= pyal.select_trials(df_, df_.result== 'R')
    df_= pyal.select_trials(df_, df_.epoch=='BL')
    
    assert np.all(df_.bin_size == .01), 'bin size is not consistent!'
    df_ = pyal.combine_time_bins(df_, int(BIN_SIZE/.01))
    for signal in time_signals:
        df_ = pyal.sqrt_transform_signal(df_, signal)
        
    df_= pyal.add_firing_rates(df_, 'smooth', std=0.05)

    #TODO: not true for all 
    #fix target locations
    df_['target_center'] = [np.reshape(x.T.flatten(), x.shape, order = 'C') for x in df_['target_center']]
    
    return df_

def get_reaches_df(df):
    df_reaches = pd.DataFrame()
    df[['idx_go_cue1',
        'idx_go_cue2',
        'idx_go_cue3',
        'idx_go_cue4']] = pd.DataFrame(df.idx_go_cue.tolist(), index= df.index)

    #separate by reaches
    for i in range(4):
        start_point_name = 'idx_go_cue' +str(i+1)
        end_point_name = ('idx_go_cue' + str(i+2)) if i < 3 else 'idx_trial_end'
        df_ = pyal.restrict_to_interval(df, start_point_name, end_point_name)
        if i>0:
            df_['start_center'] = [x[i-1] for x in df_['target_center']] #assume reach starts in target of prev reach
        df_['target_center'] = [x[i] for x in df_['target_center']]
        df_['reach'] = i+1
        df_['idx_go_cue']= df_[start_point_name]
        df_['idx_reach_end'] = df_[end_point_name] - df_[start_point_name] 
        df_reaches = pd.concat([df_reaches, df_])
    df_reaches = df_reaches.sort_values(by=['trial_id', 'reach'])
    df_reaches = df_reaches[df_reaches.idx_reach_end < 200] #TODO: better cutoff for slow reaches

    #fix pos offset
    df_second_reaches = df_reaches[df_reaches.reach == 2] #look at offset for second reach
    idx = [all(np.abs(x) < 100) for x in df_second_reaches.start_center] #remove wrong positions
    df_second_reaches = df_second_reaches[idx]
    mean_offset = np.mean([pos[0] - start_center for start_center,pos in zip(df_second_reaches.start_center, df_second_reaches.pos)],axis = 0) 
    df_reaches['pos'] = [x - mean_offset for x in df_reaches['pos']] 

    #center targets and pos at origin
    df_reaches['dist_start_center'] = [np.linalg.norm(x[0]) for x in df_reaches.pos]
    df_reaches['pos_centered'] = [x - x[0] for x in df_reaches.pos]
    df_reaches['target_centered'] = [y - x[0] for x,y in zip(df_reaches.pos,df_reaches.target_center)]

    # get target angle and group
    df_reaches['target_angle'] = [math.degrees(math.atan2(x[1],x[0])) for x in df_reaches.target_centered]
    df_reaches['target_angle'] = [360+x if x < 0 else x for x in df_reaches.target_angle]
    df_reaches['target_group'] = [math.floor(x/(360/n_target_groups)) for x in df_reaches.target_angle]

    return df_reaches
    

# def custom_r2_func(y_true, y_pred):
#     "$R^2$ value as squared correlation coefficient, as per Gallego, NN 2020"
#     c = np.corrcoef(y_true.T, y_pred.T) ** 2
#     return np.diag(c[-int(c.shape[0]/2):,:int(c.shape[1]/2)])

# custom_r2_scorer = make_scorer(custom_r2_func)

# def get_data_array_and_vel(data_list: list[pd.DataFrame], epoch , area: str ='M1', n_components: int =10) -> np.ndarray:
#     """
#     Applies PCA to the data and return a data matrix of the shape: sessions x targets x  trials x time x PCs
#     with the minimum number of trials and timepoints shared across all the datasets/targets.
    
#     Parameters
#     ----------
#     `data_list`: list of pd.dataFrame datasets from pyal-data
#     `epoch`: an epoch function of the type `pyal.generate_epoch_fun`
#     `area`: area, either: 'M1', or 'S1', or 'PMd'

#     Returns
#     -------
#     `AllData`: np.array

#     Signature
#     -------
#     AllData = get_data_array(data_list, execution_epoch, area='M1', n_components=10)
#     all_data = np.reshape(AllData, (-1,10))
#     """
#     field = f'{area}_rates'
#     n_shared_trial = np.inf
#     for df in data_list:
#         for target in range(n_targets):
#             df_ = pyal.select_trials(df, df.target_id== target)
#             n_shared_trial = np.min((df_.shape[0], n_shared_trial))

#     n_shared_trial = int(n_shared_trial)

#     # finding the number of timepoints
#     df_ = pyal.restrict_to_interval(df_,epoch_fun=epoch)
#     n_timepoints = int(df_[field][0].shape[0])

#     # pre-allocating the data matrix
#     AllData = np.empty((len(data_list), n_targets, n_shared_trial, n_timepoints, n_components))
#     AllVel  = np.empty((len(data_list), n_targets, n_shared_trial, n_timepoints, 2))
#     for session, df in enumerate(data_list):
#         df_ = pyal.restrict_to_interval(df, epoch_fun=epoch)
#         rates = np.concatenate(df_[field].values, axis=0)
#         rates_model = PCA(n_components=n_components, svd_solver='full').fit(rates)
#         df_ = pyal.apply_dim_reduce_model(df_, rates_model, field, '_pca')
#         vel_mean = np.nanmean(pyal.concat_trials(df, 'pos'), axis=0)

#         for target in range(n_targets):
#             df__ = pyal.select_trials(df_, df_.target_id==target)
#             all_id = df__.trial_id.to_numpy()
#             rng.shuffle(all_id)
#             # select the right number of trials to each target
#             df__ = pyal.select_trials(df__, lambda trial: trial.trial_id in all_id[:n_shared_trial])
#             for trial, (trial_rates,trial_vel) in enumerate(zip(df__._pca, df__.pos)):
#                 AllData[session,target,trial, :, :] = trial_rates
#                 AllVel[session,target,trial, :, :] = trial_vel - vel_mean

#     return AllData, AllVel

# def time_trim(a,b):
#     l = min(a.shape[0],b.shape[0])
#     return a[:l],b[:l]

# def _get_data_array(data_list: list[pd.DataFrame], epoch_L: int =None , area: str ='M1', model=None) -> np.ndarray:
#     "Similat to `get_data_array` only returns an apoch of length `epoch_L` randomly chosen along each trial"
#     if isinstance(data_list, pd.DataFrame):
#         data_list = [data_list]
#     if isinstance(model, int):
#         model = PCA(n_components=model, svd_solver='full')
    
#     field = f'{area}_rates'
#     n_shared_trial = np.inf
#     target_ids = np.unique(data_list[0].target_id)
#     for df in data_list:
#         for target in target_ids:
#             df_ = pyal.select_trials(df, df.target_id== target)
#             n_shared_trial = np.min((df_.shape[0], n_shared_trial))

#     n_shared_trial = int(n_shared_trial)

#     # finding the number of timepoints
#     n_timepoints = int(df_[field][0].shape[0])
#     # n_timepoints = int(df_[field][0].shape[0])
#     if epoch_L is None:
#         epoch_L = n_timepoints
#     else:
#         assert epoch_L < n_timepoints, 'Epoch longer than data'
    
#     # pre-allocating the data matrix
#     AllData = np.zeros((len(data_list), len(target_ids), n_shared_trial, epoch_L, model.n_components))

#     for session, df in enumerate(data_list):
#         rates = np.concatenate(df[field].values, axis=0)
#         rates_model = model.fit(rates)
#         df_ = pyal.apply_dim_reduce_model(df, rates_model, field, '_pca');

#         for targetIdx,target in enumerate(target_ids):
#             df__ = pyal.select_trials(df_, df_.target_id==target)
#             all_id = df__.trial_id.to_numpy()
#             # to guarantee shuffled ids
#             rng.shuffle(all_id)
#             # select the right number of trials to each target
#             df__ = pyal.select_trials(df__, lambda trial: trial.trial_id in all_id[:n_shared_trial])
#             for trial, trial_rates in enumerate(df__._pca):
#                 time_idx = rng.integers(trial_rates.shape[0]-epoch_L)
#                 trial_data = trial_rates[time_idx:time_idx+epoch_L,:]
#                 AllData[session,targetIdx,trial, :, :] = trial_data

#     return AllData
