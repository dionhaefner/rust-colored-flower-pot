# Glossary

| Term                     | Meaning in RCFP                                                                                    |
| ------------------------ | -------------------------------------------------------------------------------------------------- |
| Raster                   | Georeferenced grid of values plus CRS/transform and validity information                           |
| Band                     | One value channel registered separately for dataset selection                                      |
| COG                      | Cloud-optimized GeoTIFF with block/overview layout supporting selective range reads                |
| Catalog / metadata store | Database mapping ordered keys to paths and metadata. Imagery is stored separately                  |
| Key                      | A named dimension in the ordered dataset identity, such as scene or band                           |
| Dataset                  | A key-value tuple identifying one registered raster source                                         |
| CRS                      | Coordinate reference system used to locate source pixels                                           |
| Tile                     | An image region selected through XYZ zoom/x/y coordinates                                          |
| Preview                  | An image of the source extent, sampled to a requested output size                                  |
| Mask / nodata            | Information identifying invalid source coverage                                                    |
| Stretch                  | Mapping a data-value interval to display intensities                                               |
| Profile                  | Immutable native or terracotta-0.10.1 process/application compatibility policy                     |
| Protocol version         | Identity used by HTTP/API clients. Separate from package and catalog versions                      |
| abi3                     | CPython limited-API extension tag supporting the documented Python versions on a matching platform |
