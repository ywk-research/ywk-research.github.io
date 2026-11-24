import io, urllib.request, openpyxl
u="https://thedocs.worldbank.org/en/doc/74e8be41ceb20fa0da750cda2f6b9e4e-0050012026/related/CMO-Historical-Data-Monthly.xlsx"
b=urllib.request.urlopen(urllib.request.Request(u,headers={"User-Agent":"Mozilla/5.0 (ywk-research.github.io data job)"}),timeout=60).read()
print("bytes",len(b))
wb=openpyxl.load_workbook(io.BytesIO(b),read_only=True,data_only=True)
print(wb.sheetnames)
ws=wb["Monthly Prices"]
rows=list(ws.iter_rows(values_only=True))
for i,r in enumerate(rows[:8]): print(i, r[:75])
for r in rows[-2:]: print(r[:75])
