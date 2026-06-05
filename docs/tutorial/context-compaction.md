tail -f /mnt/workspace/siRNA/OligoFormer/logs_gpu/run_all_gpu.log
  File "/mnt/workspace/siRNA/OligoFormer/run_patent_standard_pcc.py", line 766, in main
    verify_rnafm_files(df, args)
  File "/mnt/workspace/siRNA/OligoFormer/run_patent_standard_pcc.py", line 477, in verify_rnafm_files
    raise FileNotFoundError(
FileNotFoundError: RNA-FM representations are missing. Generate them first; examples:
/mnt/workspace/siRNA/OligoFormer/data/patent_rnafm/RNAFM/patent_standard_siRNA/representations/sirna_e0927a2fe8919caa.npy
/mnt/workspace/siRNA/OligoFormer/data/patent_rnafm/RNAFM/patent_standard_siRNA/representations/sirna_4144b534cfc7b0b0.npy
/mnt/workspace/siRNA/OligoFormer/data/patent_rnafm/RNAFM/patent_standard_siRNA/representations/sirna_2b17359644224f1f.npy
DONE
Results: /mnt/workspace/siRNA/OligoFormer/test_results/patent_standard_rnafm_5fold_gpu
