\<a id="7">\</a>

## 7. Building the Analysis Dataset

The three Project CCHAIN tables have different geographic and temporal levels:

- `disease_pidsr_totals.csv` contains **weekly disease records at the city level**.

* `climate_atmosphere.csv` contains **daily climate records at the barangay level**.
* `location.csv` connects each barangay (`adm4_pcode`) to its corresponding city (`adm3_pcode`).

Before exploratory analysis can be performed, the climate and disease data must therefore be converted into the same unit of analysis:

> **one city per week**

The following steps are used:

1. Filter the disease dataset to leptospirosis cases only.
2. Attach city information to the disease records.
3. Attach city information to the barangay-level climate records.
4. Average daily climate observations across all barangays belonging to the same city.
5. Convert the daily climate data into weekly values.
6. Merge the weekly climate data with the weekly leptospirosis records.

### 7.1 Filter the Disease Dataset

```python
# Keep only leptospirosis records
df_lepto = disease[
    disease['disease_common_name'] == 'LEPTOSPIROSIS'
].copy()

print("Leptospirosis records:", df_lepto.shape)
```

### 7.2 Create a City-Level Location Reference

The `location.csv` table contains one row for each barangay, meaning that the same city appears multiple times. A unique city reference is therefore created before it is merged with the disease data.

```python
# Create a unique city-level reference table
df_cities = location[
    [
        'adm1_en',
        'adm1_pcode',
        'adm2_en',
        'adm2_pcode',
        'adm3_en',
        'adm3_pcode'
    ]
].drop_duplicates()

# Add city names and geographic information to disease records
df_lepto_loc = df_lepto.merge(
    df_cities,
    on='adm3_pcode',
    how='left'
)

df_lepto_loc.head()
```

### 7.3 Add City Information to the Climate Dataset

Climate observations are recorded at the barangay level. The location table is therefore used to identify the city to which every barangay belongs.

```python
df_climate_loc = climate.merge(
    location,
    on='adm4_pcode',
    how='left'
)

df_climate_loc.head()
```

### 7.4 Convert Climate and Disease Dates

```python
df_climate_loc['date'] = pd.to_datetime(
    df_climate_loc['date']
)

df_lepto_loc['date'] = pd.to_datetime(
    df_lepto_loc['date']
)
```

### 7.5 Aggregate Barangay Climate Data to the City Level

Several barangays belong to each city. Their climate observations are first averaged to obtain one climate observation per city per day.

```python
climate_city_daily = (
    df_climate_loc
    .groupby(['adm3_pcode', 'date'])
    .agg({
        'pr': 'mean',
        'tave': 'mean',
        'heat_index': 'mean'
    })
    .reset_index()
)

climate_city_daily.head()
```

For each city:

- `pr` represents the average daily rainfall across its barangays;
- `tave` represents the average daily temperature; and
- `heat_index` represents the average daily heat index.

### 7.6 Convert Daily Climate Data to Weekly Data

The disease dataset is weekly, so the climate observations must also be converted to weekly observations.

```python
# Convert each date to its corresponding week
climate_city_daily['week_date'] = (
    climate_city_daily['date']
    .dt.to_period('W')
    .dt.start_time
)

# Aggregate daily climate observations into weekly observations
climate_city_weekly = (
    climate_city_daily
    .groupby(['adm3_pcode', 'week_date'])
    .agg({
        'pr': 'sum',
        'tave': 'mean',
        'heat_index': 'mean'
    })
    .reset_index()
)

climate_city_weekly.head()
```

Weekly rainfall is calculated as a **sum**, while temperature and heat index are represented by their **weekly means**.

### 7.7 Merge Weekly Climate and Disease Data

```python
df_final = df_lepto_loc.merge(
    climate_city_weekly,
    left_on=['adm3_pcode', 'date'],
    right_on=['adm3_pcode', 'week_date'],
    how='left'
)

df_final.head()
```

The resulting `df_final` dataset is the main dataset used for the exploratory data analysis.

### 7.8 Check the Final Dataset

```python
print("Final dataset shape:", df_final.shape)
print(
    "Number of cities:",
    df_final['adm3_en'].nunique()
)
print(
    "Date range:",
    df_final['date'].min(),
    "to",
    df_final['date'].max()
)

print("\nMissing values:")
print(
    df_final[
        ['case_total', 'pr', 'tave', 'heat_index']
    ].isna().sum()
)
```

In the original EDA run, the final dataset contained:

- **12 cities**
- **780 weeks per city**
- **9,360 city-week observations**
- weekly observations covering **2008–2022**

The final values should be verified again when the group runs the completed notebook using the final copy of the CCHAIN datasets.

---

\<a id="8">\</a>

## 8. Exploratory Data Analysis

The exploratory data analysis examines the available leptospirosis records, selects a suitable city for detailed modeling, and investigates how rainfall and other climate variables are related to weekly leptospirosis cases.

Because changes in weather may affect leptospirosis cases only after some time has passed, the analysis also examines **lagged rainfall**.

A rainfall lag simply means rainfall from an earlier week.

For example:

- Lag 0 = rainfall during the current week
- Lag 1 = rainfall one week earlier
- Lag 2 = rainfall two weeks earlier
- Lag 4 = rainfall four weeks earlier

The EDA identifies statistical **associations**, not proof that one variable directly causes another.

### 8.1 Descriptive Statistics

The first step is to inspect the general distribution of the variables used in the analysis.

```python
cols_of_interest = [
    'case_total',
    'pr',
    'tave',
    'heat_index'
]

df_final[cols_of_interest].describe()
```

In the original EDA run, the overall descriptive statistics were approximately:

| Statistic          | Cases | Rainfall | Avg. Temperature | Heat Index |
| ------------------ | ----: | -------: | ---------------: | ---------: |
| Mean               | 0.858 |   46.747 |           27.138 |     30.066 |
| Standard deviation | 7.313 |   51.700 |            1.184 |      2.239 |
| Minimum            |     0 |        0 |           21.915 |     21.950 |
| Median             |     0 |   33.466 |           27.034 |     29.853 |
| Maximum            |   554 |  528.474 |           31.834 |     38.266 |

The disease data contain many weeks with zero reported cases and a smaller number of weeks with substantially higher case counts. This is partly because leptospirosis activity differs considerably across the 12 cities.

---

### 8.2 Research Question 1: Which City Is Most Suitable for Weekly Prediction?

The 12 cities are compared according to:

- total reported cases;
- number of weeks with at least one case;
- percentage of weeks with at least one case;
- maximum weekly cases; and
- average weekly cases.

```python
# Identify whether each week had at least one reported case
df_final['has_cases'] = (
    df_final['case_total'] > 0
)

city_suitability = (
    df_final
    .groupby('adm3_en')
    .agg(
        total_cases=('case_total', 'sum'),
        weeks_with_cases=('has_cases', 'sum'),
        total_weeks=('case_total', 'count'),
        max_weekly_cases=('case_total', 'max'),
        mean_weekly_cases=('case_total', 'mean')
    )
)

city_suitability['pct_weeks_active'] = (
    city_suitability['weeks_with_cases']
    / city_suitability['total_weeks']
    * 100
)

city_suitability = (
    city_suitability
    .sort_values(
        by='pct_weeks_active',
        ascending=False
    )
)

city_suitability
```

The original EDA found that **Iloilo City had the strongest disease-data availability among the 12 cities**.

Iloilo City recorded approximately:

- **3,948 total leptospirosis cases**
- **561 weeks with at least one case**
- **780 observed weeks**
- cases in approximately **71.9% of all weeks**
- approximately **5.06 cases per week on average**

The difference between cities can also be visualized:

```python
city_totals = (
    df_final
    .groupby('adm3_en')['case_total']
    .sum()
    .sort_values(ascending=False)
)

plt.figure(figsize=(10, 5))

city_totals.plot(kind='bar')

plt.title(
    'Total Leptospirosis Cases by City'
)
plt.xlabel('City')
plt.ylabel('Total Cases')
plt.xticks(
    rotation=45,
    ha='right'
)

plt.tight_layout()
plt.show()
```

**Finding:** Iloilo City was selected as the focus city because it had both the **largest number of total cases** and the **highest proportion of weeks with reported cases**, giving it a relatively dense weekly time series for forecasting.

---

### 8.3 Research Question 2: How Long Is the Delay Between Rainfall and Leptospirosis Cases?

The analysis next examines whether rainfall from previous weeks is more strongly associated with current leptospirosis cases than rainfall during the current week.

Only Iloilo City is used from this point onward because it was selected as the focus city.

```python
iloilo_df = (
    df_final[
        df_final['adm3_en'] == 'Iloilo City'
    ]
    .copy()
)

iloilo_df = (
    iloilo_df
    .sort_values('date')
    .reset_index(drop=True)
)

# Create rainfall from previous weeks
for lag in range(1, 5):
    iloilo_df[f'pr_lag{lag}'] = (
        iloilo_df['pr'].shift(lag)
    )
```

The relationship between rainfall and current leptospirosis cases is measured using **Spearman correlation**.

```python
correlations = {
    'Same Week (Lag 0)':
        iloilo_df['case_total'].corr(
            iloilo_df['pr'],
            method='spearman'
        ),

    '1 Week Earlier (Lag 1)':
        iloilo_df['case_total'].corr(
            iloilo_df['pr_lag1'],
            method='spearman'
        ),

    '2 Weeks Earlier (Lag 2)':
        iloilo_df['case_total'].corr(
            iloilo_df['pr_lag2'],
            method='spearman'
        ),

    '3 Weeks Earlier (Lag 3)':
        iloilo_df['case_total'].corr(
            iloilo_df['pr_lag3'],
            method='spearman'
        ),

    '4 Weeks Earlier (Lag 4)':
        iloilo_df['case_total'].corr(
            iloilo_df['pr_lag4'],
            method='spearman'
        )
}

lag_table = (
    pd.Series(correlations)
    .reset_index()
)

lag_table.columns = [
    'Rainfall Timing',
    'Spearman Correlation'
]

lag_table
```

The original EDA produced approximately:

| Rainfall timing     | Spearman correlation |
| ------------------- | -------------------: |
| Same week           |                0.212 |
| 1 week earlier      |                0.281 |
| 2 weeks earlier     |                0.341 |
| 3 weeks earlier     |                0.405 |
| **4 weeks earlier** |            **0.413** |

The relationship can also be plotted:

```python
plt.figure(figsize=(8, 4))

plt.plot(
    lag_table['Rainfall Timing'],
    lag_table['Spearman Correlation'],
    marker='o',
    linewidth=2
)

plt.title(
    'Iloilo City: Rainfall Timing '
    'and Leptospirosis Cases'
)

plt.xlabel('Timing of Rainfall')
plt.ylabel('Spearman Correlation')
plt.xticks(rotation=15)

plt.grid(
    True,
    linestyle='--',
    alpha=0.5
)

plt.tight_layout()
plt.show()
```

**Finding:** Rainfall from previous weeks has a stronger association with current leptospirosis cases than rainfall during the same week.

Among the tested lags, the strongest association occurred for rainfall **four weeks earlier** (`ρ ≈ 0.413`), closely followed by rainfall **three weeks earlier** (`ρ ≈ 0.405`).

This suggests that rainfall occurring approximately **three to four weeks before reported cases** may provide useful information for forecasting.

However, this does not prove that rainfall directly causes leptospirosis cases exactly three or four weeks later.

---

### 8.4 Research Question 3: Which Climate Variables Are Most Related to Leptospirosis Cases?

The analysis compares current rainfall, lagged rainfall, average temperature, and heat index with leptospirosis cases in Iloilo City.

```python
climate_vars = [
    'case_total',
    'pr',
    'pr_lag1',
    'pr_lag2',
    'pr_lag3',
    'pr_lag4',
    'tave',
    'heat_index'
]

corr_matrix = (
    iloilo_df[climate_vars]
    .corr(method='spearman')
)

print(
    "--- Iloilo City: Correlation "
    "with Leptospirosis Cases ---"
)

print(
    corr_matrix['case_total']
    .sort_values(ascending=False)
)
```

The original EDA produced approximately:

| Variable            | Correlation with cases |
| ------------------- | ---------------------: |
| Rainfall lag 4      |              **0.413** |
| Rainfall lag 3      |              **0.405** |
| Rainfall lag 2      |                  0.341 |
| Rainfall lag 1      |                  0.281 |
| Current rainfall    |                  0.212 |
| Heat index          |                 -0.222 |
| Average temperature |                 -0.259 |

The relationships can also be displayed using a correlation heatmap:

```python
plt.figure(figsize=(8, 6))

sns.heatmap(
    corr_matrix,
    annot=True,
    fmt='.2f',
    cmap='coolwarm',
    vmin=-1,
    vmax=1,
    square=True,
    linewidths=0.5
)

plt.title(
    'Iloilo City: Correlation Between '
    'Leptospirosis Cases and Climate Variables'
)

plt.tight_layout()
plt.show()
```

**Finding:** Lagged rainfall showed the strongest positive relationship with leptospirosis cases among the climate variables included in the current analysis.

Average temperature and heat index showed weaker negative relationships with cases.

These results do not mean that rainfall is automatically the best forecasting variable. Actual forecasting performance still needs to be tested.

---

### 8.5 Research Question 4: Does a Climate-Based Regression Model Perform Better Than a Simple Baseline?

Correlation alone is not enough to determine whether climate data improve forecasts.

A preliminary Poisson regression is therefore compared against a simple **four-week moving-average baseline**.

The baseline predicts the current week's case count based on the average number of cases observed during the previous four weeks.

> **Note:** The original EDA called this a "seasonal baseline." That label was incorrect. The code calculates a four-week moving average, not a seasonal baseline.

First, remove the initial weeks without enough lag history:

```python
df_model = (
    iloilo_df
    .dropna(subset=['pr_lag4'])
    .copy()
)
```

A chronological 80/20 train-test split is then used.

```python
split_idx = int(
    len(df_model) * 0.8
)

train = (
    df_model
    .iloc[:split_idx]
    .copy()
)

test = (
    df_model
    .iloc[split_idx:]
    .copy()
)
```

Unlike a random split, this ensures that older observations are used to predict later observations.

#### Four-Week Moving-Average Baseline

```python
# Combine train and test case histories so the first test
# prediction can still use the last observed training weeks
case_history = pd.concat(
    [
        train['case_total'],
        test['case_total']
    ]
)

baseline_predictions = (
    case_history
    .shift(1)
    .rolling(window=4)
    .mean()
)

test['pred_baseline'] = (
    baseline_predictions.loc[test.index]
)
```

#### Poisson Regression Using Climate Variables

```python
features = [
    'pr',
    'pr_lag1',
    'pr_lag2',
    'pr_lag3',
    'pr_lag4',
    'heat_index'
]

X_train = sm.add_constant(
    train[features]
)

y_train = train['case_total']

X_test = sm.add_constant(
    test[features],
    has_constant='add'
)

y_test = test['case_total']

model_reg = sm.GLM(
    y_train,
    X_train,
    family=sm.families.Poisson()
).fit()

test['pred_regression'] = (
    model_reg.predict(X_test)
)
```

#### Model Evaluation

The models are compared using:

- **MAE — Mean Absolute Error**
- **RMSE — Root Mean Squared Error**

For both metrics, **lower values indicate better predictions**.

```python
mae_baseline = mean_absolute_error(
    y_test,
    test['pred_baseline']
)

rmse_baseline = np.sqrt(
    mean_squared_error(
        y_test,
        test['pred_baseline']
    )
)

mae_reg = mean_absolute_error(
    y_test,
    test['pred_regression']
)

rmse_reg = np.sqrt(
    mean_squared_error(
        y_test,
        test['pred_regression']
    )
)

comparison = pd.DataFrame({
    'Model': [
        '4-Week Moving-Average Baseline',
        'Lagged Climate Poisson Regression'
    ],
    'MAE': [
        mae_baseline,
        mae_reg
    ],
    'RMSE': [
        rmse_baseline,
        rmse_reg
    ]
})

comparison
```

The original EDA obtained approximately:

| Model                              |       MAE |      RMSE |
| ---------------------------------- | --------: | --------: |
| **4-week moving-average baseline** | **1.644** | **2.451** |
| Lagged-climate Poisson regression  |     3.419 |     4.291 |

**Finding:** The preliminary Poisson regression did **not** outperform the simple recent-case baseline.

This does not mean that climate variables are useless. It means that the initial combination of climate variables and a basic Poisson regression was not sufficient to produce better forecasts.

Phase 2 should therefore test:

- additional feature engineering;
- other regression and count models;
- additional climate variables;
- seasonal information; and
- stronger forecasting baselines.

A true seasonal baseline, such as using approximately the same week from the previous year, should also be evaluated during Phase 2.

---

\<a id="9">\</a>

## 9. Summary of EDA Findings

The exploratory analysis produced five main findings that guide the planned Phase 2 modeling.

| # | Finding                                                                                                                                                                                       | Evidence    | Implication for Phase 2                                                                                     |
| - | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------- | ----------------------------------------------------------------------------------------------------------- |
| 1 | **Iloilo City has the strongest leptospirosis data availability among the 12 CCHAIN cities.** It recorded approximately 3,948 cases and had cases during 561 of 780 observed weeks (71.9%).   | Section 8.2 | Use Iloilo City as the focus location for forecasting.                                                      |
| 2 | **Previous rainfall is more strongly associated with current cases than rainfall during the same week.**                                                                                      | Section 8.3 | Include lagged rainfall variables in the forecasting dataset.                                               |
| 3 | **The strongest rainfall associations among the tested lags occurred around three to four weeks earlier.** Lag 3 had `ρ ≈ 0.405`, while Lag 4 had `ρ ≈ 0.413`.                                | Section 8.3 | Initially evaluate rainfall lags around 1–4 weeks and determine the best combination using validation data. |
| 4 | **Lagged rainfall had a stronger positive association with cases than the other climate variables currently tested.** Average temperature and heat index showed weaker negative associations. | Section 8.4 | Prioritize lagged rainfall while testing whether other climate variables improve forecasting performance.   |
| 5 | **The initial lagged-climate Poisson regression did not outperform the four-week moving-average baseline.**                                                                                   | Section 8.5 | Phase 2 should test additional models, feature engineering methods, and stronger forecasting baselines.     |

### Overall EDA Conclusion

Based on the available CCHAIN records, **Iloilo City was selected as the focus city** because it contains the densest leptospirosis time series among the 12 cities included in the project.

The EDA also identified a delayed relationship between rainfall and reported leptospirosis cases. Rainfall occurring approximately **three to four weeks earlier** showed a stronger association with current case counts than rainfall occurring during the current week.

This result supports the use of **lagged climate variables** during Phase 2.

However, the preliminary forecasting experiment showed that a basic climate-based Poisson regression was not automatically better than a simple model based on recent case history. This indicates that the relationship between climate and reported leptospirosis cases is more complex than a single correlation.

Phase 2 will therefore evaluate alternative models, additional climate features, different lag combinations, seasonal information, and appropriate forecasting baselines using chronological training, validation, and test periods.

The findings from this EDA should be interpreted as **associations rather than evidence of direct causation**.
