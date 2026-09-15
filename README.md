# Extreme Weather Watch

## Introduction

The value proposition of this website is to collect extreme weather events from news/social media channels/other weather apps/etc. in a single space. Users can see, at a glance, what is happening around the world, weather-wise. The website would cluster these extreme weather events, catalogue them by type, strength, etc. and provide users quick access to:

- written summary information related to the event (start date, ongoing, size, damages, n injured, missing, dead, etc.)
- pictures and videos of the event pulled from social media, prominent people (politicians, meteorologists), newspapers, etc.
- social media posts related to the event (mainly x, bluesky, instagram, reddit)
- current weather conditions + 3/5 days weather forecast

Ideally, the website should present itself like [https://zoom.earth/](https://zoom.earth/) or Google Maps/OpenMaps. It should be a world map, easy to navigate, zoom in, etc. It should be easy to pin your location using geolocation data, with the map centred on where you are.

The map would display extreme weather events over the last X days using different icons. There would be filters you can choose to show only events of a specific type / scale / timeframe. (e.g. only fires bigger than X in the last Y days).

The map would be interactive; by clicking on the dot of the natural event (e.g. red for fire, blue for flood, etc.) we would be able to see the photos/videos of the event, the headlines, maybe a feed (as explained above)

Eventually, if the website gets enough traction, we would introduce the possibility for a user to make their own account and submit information for an event. An event could be created by said user or they could join an already existing event. The user would be able to add pictures/videos/links but also enrich the description of the event. Practically making this a Wikipedia for natural disasters. Ultimately, the end-goal of the website would be to have first-hand user input to document extreme weather events, without changing the platform.

We realise this is too far ahead, so the first step would be to have a platform that finds (1) catalogues (2) and displays events without users' input. However, it is useful to consider this when designing a proper database structure to accommodate this future feature. No frontend is needed for this right now, as I would be interested mainly in the MVP right now: data collection, data presentation, website usability, front-end design, etc.

Slides: [https://docs.google.com/presentation/d/1ue2GXbwYTAzx-kkgJ25d_5NGQZNjg_IyWjjkkUGcYhA/edit#slide=id.g2759e7532a8_0_374](https://docs.google.com/presentation/d/1ue2GXbwYTAzx-kkgJ25d_5NGQZNjg_IyWjjkkUGcYhA/edit#slide=id.g2759e7532a8_0_374)

## Proof of Concept

Steps/points:

1. Version control via GitHub.
2. Aggregate data either via a “news” API or via web scraping. (Interesting to research if an LLM swarm + bots + search with a browser such as Browserbase would be sufficient since parsing websites is too complex)
3. Save the data in a database (cloud-based?/postgreSQL? not sure what is best, to investigate) (critical to have a method to properly aggregate events, e.g. if 5/10 posts/news articles are about the same event we should be able to understand this and group these)
4. Normalise, clean and enrich the data. Add approximate (or precise depending on the information available) location pins (even multiple pins for the same article - e.g. if it mentions streets or multiple cities).
5. Display data via a website:
6. Add a “donate” button (for later) :)

## Links

Several websites collect and visualise weather events on maps. Here are some of the best options.

Great maps:

- [https://earth.nullschool.net/](https://earth.nullschool.net/)
- [https://zoom.earth/](https://zoom.earth/)
- [https://worldview.earthdata.nasa.gov/](https://worldview.earthdata.nasa.gov/)
- [https://www.wxcharts.com/](https://www.wxcharts.com/)
- [https://www.weather.gov/](https://www.weather.gov/)
- [https://www.msn.com/en-gb/weather/maps/?type=radar](https://www.msn.com/en-gb/weather/maps/?type=radar)

Other maps:

- [https://www.openstreetmap.org/](https://www.openstreetmap.org/#map=8/52.154/5.295)
- [https://what3words.com/](https://what3words.com/)

Good weather data:

- [https://weatherspark.com/](https://weatherspark.com/)
- [https://www.weatherpro.com/en/nl](https://www.weatherpro.com/en/nl)
- [https://charts.ecmwf.int/](https://charts.ecmwf.int/)

Collection of catastrophic events data:

- [https://www.emdat.be/](https://www.emdat.be/) (good website we could take data from?)
- [https://gdacs.org/](https://gdacs.org/)
- [https://disasters-nasa.hub.arcgis.com/](https://disasters-nasa.hub.arcgis.com/)
- [https://reliefweb.int/disasters](https://reliefweb.int/disasters)

Data on climate change:

- [https://climatechangetracker.org/](https://climatechangetracker.org/)
- [https://atlas.climate.copernicus.eu/atlas](https://atlas.climate.copernicus.eu/atlas)
- [https://climatereanalyzer.org/](https://climatereanalyzer.org/)
- [https://cds.climate.copernicus.eu/](https://cds.climate.copernicus.eu/) (API?)

Repos/Documentation:

- [github.com/sunshineplan/weather](https://github.com/sunshineplan/weather)
- [https://gitlab.com/KNMI-OSS/KNMI-App](https://gitlab.com/KNMI-OSS/KNMI-App)
