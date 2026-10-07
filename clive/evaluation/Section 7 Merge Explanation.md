### Preparing the Geographic Data

The datasets use different geographic identifiers:

- `adm4_pcode` identifies a **barangay**.
- `adm3_pcode` identifies a **city**.

The disease dataset is already recorded at the city level using `adm3_pcode`, while the climate dataset is recorded at the barangay level using `adm4_pcode`.

The location table is therefore used to connect the barangay-level climate observations to their corresponding cities.