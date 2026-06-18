# Build the records ETL

Read `/app/data/records.csv`, drop rows whose `status` is `void`, and write the
surviving rows to `/app/out/clean.csv` preserving header order. The number of
surviving rows must equal the count of non-void input rows.
