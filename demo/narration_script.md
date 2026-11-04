# OjoVial narration script

Video length: 2:26. Read at a relaxed pace, and start each section when the timer reaches its start time.

## 0:00 to 0:20: The problem (title and problem cards)
In San José, at intersections like the Circunvalación, city cameras work as disconnected silos. Following one vehicle from one camera to the next means scrubbing through footage by hand. This is OjoVial, my attempt at closing that gap with Meta's models.

## 0:20 to 0:33: The testbed (both cameras playing)
I don't have access to city camera feeds, so I built a small version on my table: two cameras at different resolutions, a real toy car, and obstacles in its way.

## 0:33 to 0:48: Step 1 (Suggest objects, then the list of candidates)
Step one. Muse Spark names what's in the scene from the first frame of both cameras. This is a live call, sped up here, and every wait shows its real time.

## 0:48 to 0:58: Step 2 begins (typing "red toy car")
The car arrives after that first frame, so it isn't listed. But I can type any description. I ask SAM 3.1 for a red toy car.

## 0:58 to 1:27: Step 2 results (masks on the moving car)
SAM tracks each video separately, with no training and no clicking on the video. Tracking took about six minutes for real. Here are the masks, frame by frame, following the car through blur and a big change in scale. These strips under the timeline show when SAM has the car in each camera.

## 1:27 to 1:56: Step 3 (crops, then the verdict at 1:39)
Step three. SAM can't tell me that these two tracks are the same car, because it sees each video on its own. So the app picks the clearest shared moment and sends both crops to Muse Spark. The verdict: same object, at seventy percent confidence. It cites the bright red body, the low sports-car profile and the dark wheels, with one view from the side and the other from behind.

## 1:56 to 2:10: Saved example (Run 01)
One more, from a saved earlier run. SAM has this car in Camera B from the first frame, but picks it up in Camera A at frame 40, just after it drives into view.

## 2:10 to 2:26: Close (pipeline and outro cards)
Three steps, two Meta models: Muse Spark discovers, SAM segments, and Muse Spark verifies. It's a proof of concept, not yet tested on real traffic. This is OjoVial. I'm Guillermo Jimenez. The code is on GitHub.
