# WindEurope 72 Hours Challenge: Elevations API

## Summary

## Usage

All elevations returned are measured in meters.

### H3 cells

To request the elevations of a list of H3 cells:

```shell
curl \
  --header "Content-Type: application/json" \
  --request POST \
  --data '{"h3_cells": [630949280935159295, 630949280220393983]}' \
  https://europe-west1-windeurope72hours.cloudfunctions.net/elevations-api
```

**Notes**

- Requests of this form are limited to 15 cells per request.

### Lat/lng coordinates

To request the elevations of a list of latitude/longitude coordinates:

```shell
curl \
  --header "Content-Type: application/json" \
  --request POST \
  --data '{"coordinates": [[54.53097, 5.96836]]}' \
  https://europe-west1-windeurope72hours.cloudfunctions.net/elevations-api
```

**Notes**

- A `resolution` field can also be included - this should be one of the H3 resolution levels. The default of 12 is used if
  not included.
- Requests of this form are limited to 15 cells per request.

### H3 cells contained within a polygon

To request the elevations of the H3 cells within a polygon defined by a list of latitude/longitude coordinates:

```shell
curl \
  --header "Content-Type: application/json" \
  --request POST \
  --data '{"polygon": [[54.53097, 5.96836], [54.53075, 5.96435], [54.52926, 5.96432], [54.52903, 5.96888]]}' \
  https://europe-west1-windeurope72hours.cloudfunctions.net/elevations-api
```

**Notes**

- A `resolution` field can also be included:
  - This should be one of the H3 resolution levels
  - The default of 12 is used if not included
  - The returned cells will be of this resolution
- Requests of this form are limited to polygons that contain up to 1500 cells per request. You can reduce the number of
  cells within a polygon by decreasing the resolution.
