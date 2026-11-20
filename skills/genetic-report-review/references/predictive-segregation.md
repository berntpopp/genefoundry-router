# Predictive, cascade (familial variant) and segregation report checks

1. **Index case linked.** The familial variant is named exactly as in the index report
   (gene, transcript, HGVS, build) and the index report is referenced. Any difference in
   HGVS between the two reports is Major.
2. **Index classification current.** Re-check the index variant with `clinvar_get_variant`.
   Predictive testing is offered only for P/LP variants; predictive testing for a VUS is
   Critical. A downgrade since the index report must be stated.
3. **Scope wording.** "Only the familial variant was tested; the gene was not fully
   analysed." A negative result means the person did not inherit the familial variant; the
   remaining risk is the general population risk for that condition, not zero.
4. **Sample identity.** Confirmation on a second, independently drawn sample, or a stated
   reason why not; a positive control (the index sample) where the lab's policy requires it.
5. **Segregation reports.** State which relatives were tested, their affected status and
   the result for each, and what this means for the classification (co-segregation or
   de novo evidence). Phase (cis/trans) for two variants follows from the parents' results.
6. **Counselling and age.** Predictive testing of minors only for conditions with
   preventive or treatment options in childhood. In Germany a genetic counselling offer
   is required (GenDG §10), and prenatal testing must not report conditions that start in
   adulthood (GenDG §15).
