---
name: trip-planning
description: Method for planning a trip or a day out with several stops, such as a multi-day itinerary, a weekend plan, or a route through several sights in a city. Covers fixing the trip frame, checking the weather, choosing places, grouping them by area, checking travel time between stops, and laying out a realistic day-by-day plan. Not for a single weather, place, or route lookup.
metadata:
  fusion-tools: weather_forecast local_place_search route_compare
  source: "Written for Fusion"
---

# Trip planning

## 1. Fix the frame

Work out the destination, dates or number of days, starting point (hotel, station, or home), pace, and anything the user must see or wants to avoid. Use budget and travel companions only if the user mentioned them. If the destination or dates are missing and the plan depends on them, ask one short question. Otherwise state your assumptions and continue.

## 2. Check the weather early

Look up the forecast for the destination and dates when they fall within the forecast range. Use it to put outdoor stops on the better days and indoor options on wet or very hot days. If the dates are beyond the forecast range, say so and include indoor alternatives instead of guessing the weather.

## 3. Choose places

- Start with the places the user named, then fill gaps with a few well-chosen options rather than long lists.
- Search for concrete places (sights, food, and rest stops near where the user will be) instead of relying on memory for names and locations.
- When a name matches several candidates, do not pick one by distance. Show the candidates or ask which one is meant.

## 4. Group by area, then order

Cluster each day's stops by area and order them to avoid backtracking. Check travel options between stops where travel time actually matters: across cities, long distances, or tight schedules. Do not route every short hop.

## 5. Make each day realistic

Allow time for meals, transfers, and a buffer. Around three to five main stops per day suits a normal pace; fewer for a relaxed trip or with children or older travellers. Respect opening hours when you know them.

## 6. Present the plan

- Day by day: time blocks, the place, one line on why it is worth it, and how to get to the next stop with mode and time.
- Add a short weather note per day when you have a forecast.
- End with practical tips for this trip only, such as bookings to make ahead or what to pack for the forecast.
- Take times, durations, and weather from tool results. Mark anything else clearly as an estimate.
