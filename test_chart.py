import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans', 'Arial']
plt.rcParams['axes.unicode_minus'] = False

df = pd.read_excel(r"C:\Users\37535\Desktop\实训留痕迹\基本直线绕城高速重庆\location1\1-1_trajectory.xlsx", usecols=["Time", "ID"])

df["TimeBin"] = (df["Time"] // 60).astype(int)
bin_durations = df.groupby("TimeBin")["Time"].agg(lambda x: x.max() - x.min())
valid_bins = bin_durations[bin_durations >= 30].index
df = df[df["TimeBin"].isin(valid_bins)]

flow = df.groupby("TimeBin")["ID"].nunique().sort_index()

# Original plotting logic (which caused the gap)
# X-axis was: 0, 60, 120, ..., 1140 (which is the START of the bins)
time_starts = flow.index.values * 60.0
flow_vals = flow.values.astype(float)

# NEW plotting logic (which solves the gap)
# X-axis should be: 60, 120, 180, ..., 1200 (which is the END of the bins)
# Because predicting "the next 60s" from 1200 means predicting 1200-1260
time_ends = time_starts + 60.0

print("time_starts max:", time_starts[-1])
print("time_ends max:", time_ends[-1])
