import cv2
import math
import sqlite3
import os
from collections import deque
from datetime import datetime
import helm


# CONFIG  — only change values in this block
SPEED_LIMIT_CAR  = 60     # km/h
SPEED_LIMIT_BIKE = 50     # km/h
SPEED_WINDOW     = 20     # number of frames to average speed over
DETECT_EVERY     = 12     # run cascade every N frames
MIN_SPEED        = 5      # km/h — below this = parked / false detect, ignore
MAX_SPEED        = 150    # km/h — above this = tracker glitch, ignore
WIDTH, HEIGHT    = 1280, 720
VIOLATIONS_DIR   = "violations"
DB_PATH          = "violations.db"


PPM = 41  

os.makedirs(VIOLATIONS_DIR, exist_ok=True)

carCascade  = cv2.CascadeClassifier('cars.xml')
bikeCascade = cv2.CascadeClassifier('motor-v4.xml')



# DATABASE

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS violations (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            vehicle_id INTEGER,
            type       TEXT,
            speed      REAL,
            violation  TEXT,
            timestamp  TEXT,
            image_path TEXT
        )
    """)
    conn.commit()
    conn.close()


def save_violation(vehicle_id, vtype, speed, violation, image_path):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO violations (vehicle_id,type,speed,violation,timestamp,image_path) VALUES (?,?,?,?,?,?)",
        (vehicle_id, vtype, round(speed, 1), violation,
         datetime.now().strftime("%Y-%m-%d %H:%M:%S"), image_path)
    )
    conn.commit()
    conn.close()


# EVIDENCE
def capture_violation(frame, vehicle_id, vtype, speed, violation):
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:19]
    path = f"{VIOLATIONS_DIR}/{vtype}_{vehicle_id}_{ts}.jpg"
    img  = frame.copy()
    for i, line in enumerate([
        f"VIOLATION: {violation}",
        f"Type : {vtype.upper()}  ID: {vehicle_id}",
        f"Speed: {int(speed)} km/h",
        f"Time : {ts}",
    ]):
        cv2.putText(img, line, (12, 28 + i * 28),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 255), 2, cv2.LINE_AA)
    cv2.imwrite(path, img)
    return path


# VIOLATION CHECK  — fires exactly once per vehicle ID
_alerted: set = set()

def check_violation(frame, vehicle_id, vtype, speed, helmet_status=None):
    if speed < MIN_SPEED:
        return None

    flags = []
    limit = SPEED_LIMIT_CAR if vtype == "car" else SPEED_LIMIT_BIKE
    if speed > limit:
        flags.append("OverSpeed")
    if vtype == "bike" and helmet_status == "No Helmet Detected":
        flags.append("No Helmet")

    if not flags:
        return None

    violation = " + ".join(flags)

    if vehicle_id not in _alerted:
        _alerted.add(vehicle_id)
        img_path = capture_violation(frame, vehicle_id, vtype, speed, violation)
        save_violation(vehicle_id, vtype, speed, violation, img_path)
        print(f"[VIOLATION] {vtype.upper()} #{vehicle_id} | {violation} | {int(speed)} km/h")

    return violation



def already_tracked(cx, cy, loc_dict, tracker_dict):
    for tid in tracker_dict:
        lp = loc_dict.get(tid)
        if lp is None:
            continue
        lx, ly, lw, lh = lp
        
        if (lx - lw * 0.5 <= cx <= lx + lw * 1.5 and
                ly - lh * 0.5 <= cy <= ly + lh * 1.5):
            return True
    return False



def compute_speed(cur_box, prev_box, fps):
    
    if prev_box is None or fps <= 0:
        return None
    cx1 = cur_box[0]  + cur_box[2]  / 2
    cy1 = cur_box[1]  + cur_box[3]  / 2
    cx2 = prev_box[0] + prev_box[2] / 2
    cy2 = prev_box[1] + prev_box[3] / 2
    dist_px = math.hypot(cx1 - cx2, cy1 - cy2)
    dist_m  = dist_px / PPM
    # one frame = 1/fps seconds
    speed_ms  = dist_m * fps          # metres per second
    speed_kmh = speed_ms * 3.6
    return speed_kmh


def trackMultipleObjects(video_path):
    init_db()
    video = cv2.VideoCapture(video_path)

    # Get real FPS from the video file
    fps = video.get(cv2.CAP_PROP_FPS)
    if fps <= 0 or fps > 120:
        fps = 25.0   # fallback if metadata is missing
    print(f"[INFO] Video FPS: {fps}")

    video.set(cv2.CAP_PROP_FRAME_WIDTH,  WIDTH)
    video.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)

    carTracker       = {}
    bikeTracker      = {}
    carLocPrev       = {}
    bikeLocPrev      = {}
    carFailCount     = {}
    carSpeedHist     = {}
    bikeSpeedHist    = {}
    speedCar         = {}
    speedBike        = {}
    helmets          = {}
    currentCarID     = 0
    currentBikeID    = 0
    frameCounter     = 0

    C_CAR  = (255,  80,  80)
    C_BIKE = ( 80, 255,  80)
    C_VIOL = (  0,   0, 255)

    while True:
        ok, frame = video.read()
        if not ok or frame is None:
            break

        frame        = cv2.resize(frame, (WIDTH, HEIGHT))
        result       = frame.copy()
        frameCounter += 1

        # ── CAR tracker updates
        to_del = []
        for cid, tracker in carTracker.items():
            ok2, box = tracker.update(frame)
            if not ok2:
                carFailCount[cid] = carFailCount.get(cid, 0) + 1
                if carFailCount[cid] > 20:
                    to_del.append(cid)
                continue
            carFailCount[cid] = 0
            x, y, w, h = map(int, box)
            if w < 30 or h < 20 or x < 0 or y < 0 or x+w > WIDTH or y+h > HEIGHT:
                carFailCount[cid] = carFailCount.get(cid, 0) + 1
                if carFailCount[cid] > 20:
                    to_del.append(cid)
                continue

            spd = compute_speed([x, y, w, h], carLocPrev.get(cid), fps)
            if spd is not None and MIN_SPEED < spd < MAX_SPEED:
                carSpeedHist[cid].append(spd)
            speedCar[cid] = (sum(carSpeedHist[cid]) / len(carSpeedHist[cid])
                             if carSpeedHist.get(cid) else 0.0)

            carLocPrev[cid] = [x, y, w, h]

            viol   = check_violation(frame, cid, "car", speedCar[cid])
            color  = C_VIOL if viol else C_CAR
            cv2.rectangle(result, (x, y), (x+w, y+h), color, 2)
            lbl = f"Car#{cid} {int(speedCar[cid])}km/h"
            if viol: lbl += f" [{viol}]"
            cv2.putText(result, lbl, (x, max(y-6, 12)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.48, color, 1)

        for cid in to_del:
            for d in [carTracker, carLocPrev, carFailCount, carSpeedHist, speedCar]:
                d.pop(cid, None)

        # ── BIKE tracker updates 
        to_del = []
        for bid, tracker in bikeTracker.items():
            ok2, box = tracker.update(frame)
            if not ok2:
                to_del.append(bid)
                continue
            x, y, w, h = map(int, box)
            if w < 20 or h < 15 or x < 0 or y < 0 or x+w > WIDTH or y+h > HEIGHT:
                to_del.append(bid)
                continue

            spd = compute_speed([x, y, w, h], bikeLocPrev.get(bid), fps)
            if spd is not None and MIN_SPEED < spd < MAX_SPEED:
                bikeSpeedHist[bid].append(spd)
            speedBike[bid] = (sum(bikeSpeedHist[bid]) / len(bikeSpeedHist[bid])
                              if bikeSpeedHist.get(bid) else 0.0)

            bikeLocPrev[bid] = [x, y, w, h]

            # helmet check — stop once confirmed
            if helmets.get(bid, "No Helmet Detected") != "Helmet Detected":
                roi = frame[y:y+h, x:x+w]
                if roi.shape[0] > 30 and roi.shape[1] > 30:
                    if helm.detect(cv2.resize(roi, (416, 416))):
                        helmets[bid] = "Helmet Detected"

            viol  = check_violation(frame, bid, "bike",
                                    speedBike[bid],
                                    helmets.get(bid, "No Helmet Detected"))
            color = C_VIOL if viol else C_BIKE
            cv2.rectangle(result, (x, y), (x+w, y+h), color, 2)
            lbl = f"Bike#{bid} {int(speedBike[bid])}km/h {helmets.get(bid,'?')}"
            if viol: lbl += f" [{viol}]"
            cv2.putText(result, lbl, (x, max(y-6, 12)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)

        for bid in to_del:
            for d in [bikeTracker, bikeLocPrev, bikeSpeedHist, speedBike]:
                d.pop(bid, None)
            helmets.pop(bid, None)

        # ── Detection (every N frames) 
        if frameCounter % DETECT_EVERY == 0:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            # Equalise histogram — improves contrast for cascade
            gray = cv2.equalizeHist(gray)

            # HIGH minNeighbors = fewer false positives (was accidentally
            # dropped to 5 last time which made things much worse)
            detected_cars = carCascade.detectMultiScale(
                gray,
                scaleFactor=1.1,
                minNeighbors=8,       # strict: 8+ neighbours required
                minSize=(60, 40),     # minimum realistic car size on screen
                maxSize=(500, 350),   # maximum — ignores tiny distant noise
            )
            detected_bikes = bikeCascade.detectMultiScale(
                gray,
                scaleFactor=1.1,
                minNeighbors=8,
                minSize=(30, 25),
                maxSize=(300, 220),
            )

            for det, tracker_dict, loc_dict, speed_hist, speed_dict, id_counter, vtype in [
                (detected_cars,  carTracker,  carLocPrev,  carSpeedHist,  speedCar,  None, "car"),
                (detected_bikes, bikeTracker, bikeLocPrev, bikeSpeedHist, speedBike, None, "bike"),
            ]:
                # make iterable even when cascade returns empty tuple
                det = det if isinstance(det, (list,)) or (hasattr(det, '__len__') and len(det)) else []

                for (x, y, w, h) in det:
                    cx, cy = x + w / 2, y + h / 2
                    if already_tracked(cx, cy, loc_dict, tracker_dict):
                        continue

                    tr = cv2.legacy.TrackerCSRT_create()
                    tr.init(frame, (x, y, w, h))

                    if vtype == "car":
                        nid = currentCarID
                        currentCarID  += 1   # won't work — fixed below
                        carTracker[nid]      = tr
                        carLocPrev[nid]      = [x, y, w, h]
                        carFailCount[nid]    = 0
                        carSpeedHist[nid]    = deque(maxlen=SPEED_WINDOW)
                        speedCar[nid]        = 0.0
                    else:
                        nid = currentBikeID
                        currentBikeID += 1
                        bikeTracker[nid]     = tr
                        bikeLocPrev[nid]     = [x, y, w, h]
                        bikeSpeedHist[nid]   = deque(maxlen=SPEED_WINDOW)
                        speedBike[nid]       = 0.0
                        helmets[nid]         = "No Helmet Detected"

        
        conn    = sqlite3.connect(DB_PATH)
        total_v = conn.execute("SELECT COUNT(*) FROM violations").fetchone()[0]
        conn.close()

        cv2.rectangle(result, (0, 0), (420, 76), (15, 15, 15), -1)
        cv2.putText(result, f"Cars:{len(carTracker)}  Bikes:{len(bikeTracker)}  FPS:{fps:.0f}  PPM:{PPM}",
                    (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1)
        cv2.putText(result, f"Limits  Car:{SPEED_LIMIT_CAR}  Bike:{SPEED_LIMIT_BIKE} km/h",
                    (10, 44), cv2.FONT_HERSHEY_SIMPLEX, 0.5,  (170, 170, 170), 1)
        cv2.putText(result, f"Violations recorded: {total_v}",
                    (10, 66), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (60, 120, 255), 1)

        cv2.imshow("Traffic Enforcement", result)
        if cv2.waitKey(1) & 0xFF == 27:
            break
        if cv2.getWindowProperty("Traffic Enforcement", cv2.WND_PROP_VISIBLE) < 1:
            break

    video.release()
    cv2.destroyAllWindows()
    print("[INFO] Processing finished.")




def trackMultipleObjects(video_path):   # — redefinition intentional
    init_db()
    video = cv2.VideoCapture(video_path)

    fps = video.get(cv2.CAP_PROP_FPS)
    if fps <= 0 or fps > 120:
        fps = 25.0
    print(f"[INFO] Video: {video_path}  |  FPS: {fps:.1f}  |  PPM: {PPM}")

    video.set(cv2.CAP_PROP_FRAME_WIDTH,  WIDTH)
    video.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)

    carTracker    = {}; bikeTracker   = {}
    carLocPrev    = {}; bikeLocPrev   = {}
    carFailCount  = {}
    carSpeedHist  = {}; bikeSpeedHist = {}
    speedCar      = {}; speedBike     = {}
    helmets       = {}
    nextCarID     = 0
    nextBikeID    = 0
    frameCounter  = 0

    C_CAR  = (255,  80,  80)
    C_BIKE = ( 80, 220,  80)
    C_VIOL = (  0,   0, 255)

    while True:
        ok, frame = video.read()
        if not ok or frame is None:
            break

        frame        = cv2.resize(frame, (WIDTH, HEIGHT))
        result       = frame.copy()
        frameCounter += 1

        # ── update car trackers
        to_del = []
        for cid, tr in list(carTracker.items()):
            ok2, box = tr.update(frame)
            if not ok2:
                carFailCount[cid] = carFailCount.get(cid, 0) + 1
                if carFailCount[cid] > 20:
                    to_del.append(cid)
                continue
            carFailCount[cid] = 0
            x, y, w, h = map(int, box)
            if w < 30 or h < 20 or x < 0 or y < 0 or x+w > WIDTH or y+h > HEIGHT:
                carFailCount[cid] = carFailCount.get(cid, 0) + 1
                if carFailCount[cid] > 20:
                    to_del.append(cid)
                continue

            spd = compute_speed([x,y,w,h], carLocPrev.get(cid), fps)
            if spd is not None and MIN_SPEED < spd < MAX_SPEED:
                carSpeedHist[cid].append(spd)
            speedCar[cid] = (sum(carSpeedHist[cid]) / len(carSpeedHist[cid])
                             if carSpeedHist.get(cid) else 0.0)
            carLocPrev[cid] = [x, y, w, h]

            viol  = check_violation(frame, cid, "car", speedCar[cid])
            color = C_VIOL if viol else C_CAR
            cv2.rectangle(result, (x, y), (x+w, y+h), color, 2)
            lbl = f"Car#{cid} {int(speedCar[cid])}km/h"
            if viol: lbl += f" [{viol}]"
            cv2.putText(result, lbl, (x, max(y-5,12)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.48, color, 1)

        for cid in to_del:
            for d in [carTracker, carLocPrev, carFailCount, carSpeedHist, speedCar]:
                d.pop(cid, None)

        # ── update bike trackers
        to_del = []
        for bid, tr in list(bikeTracker.items()):
            ok2, box = tr.update(frame)
            if not ok2:
                to_del.append(bid); continue
            x, y, w, h = map(int, box)
            if w < 20 or h < 15 or x < 0 or y < 0 or x+w > WIDTH or y+h > HEIGHT:
                to_del.append(bid); continue

            spd = compute_speed([x,y,w,h], bikeLocPrev.get(bid), fps)
            if spd is not None and MIN_SPEED < spd < MAX_SPEED:
                bikeSpeedHist[bid].append(spd)
            speedBike[bid] = (sum(bikeSpeedHist[bid]) / len(bikeSpeedHist[bid])
                              if bikeSpeedHist.get(bid) else 0.0)
            bikeLocPrev[bid] = [x, y, w, h]

            if helmets.get(bid, "No Helmet Detected") != "Helmet Detected":
                roi = frame[y:y+h, x:x+w]
                if roi.shape[0] > 30 and roi.shape[1] > 30:
                    if helm.detect(cv2.resize(roi, (416, 416))):
                        helmets[bid] = "Helmet Detected"

            viol  = check_violation(frame, bid, "bike",
                                    speedBike[bid],
                                    helmets.get(bid, "No Helmet Detected"))
            color = C_VIOL if viol else C_BIKE
            cv2.rectangle(result, (x, y), (x+w, y+h), color, 2)
            lbl = f"Bike#{bid} {int(speedBike[bid])}km/h {helmets.get(bid,'?')}"
            if viol: lbl += f" [{viol}]"
            cv2.putText(result, lbl, (x, max(y-5,12)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)

        for bid in to_del:
            for d in [bikeTracker, bikeLocPrev, bikeSpeedHist, speedBike]:
                d.pop(bid, None)
            helmets.pop(bid, None)

        # ── cascade detection every N frames
        if frameCounter % DETECT_EVERY == 0:
            gray = cv2.equalizeHist(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))

            det_cars  = carCascade.detectMultiScale(
                gray, scaleFactor=1.1, minNeighbors=8,
                minSize=(60, 40), maxSize=(500, 350)
            )

           


            det_bikes = bikeCascade.detectMultiScale(
                gray, scaleFactor=1.1, minNeighbors=8,
                minSize=(30, 25), maxSize=(300, 220)
            )

            # register new cars
            for (x, y, w, h) in (det_cars if len(det_cars) else []):
                cx, cy = x + w/2, y + h/2
                if already_tracked(cx, cy, carLocPrev, carTracker):
                    continue
                tr = cv2.legacy.TrackerCSRT_create()
                tr.init(frame, (x, y, w, h))
                cid = nextCarID; nextCarID += 1
                carTracker[cid]   = tr
                carLocPrev[cid]   = [x, y, w, h]
                carFailCount[cid] = 0
                carSpeedHist[cid] = deque(maxlen=SPEED_WINDOW)
                speedCar[cid]     = 0.0

            # register new bikes
            for (x, y, w, h) in (det_bikes if len(det_bikes) else []):
                cx, cy = x + w/2, y + h/2
                if already_tracked(cx, cy, bikeLocPrev, bikeTracker):
                    continue
                tr = cv2.legacy.TrackerCSRT_create()
                tr.init(frame, (x, y, w, h))
                bid = nextBikeID; nextBikeID += 1
                bikeTracker[bid]   = tr
                bikeLocPrev[bid]   = [x, y, w, h]
                bikeSpeedHist[bid] = deque(maxlen=SPEED_WINDOW)
                speedBike[bid]     = 0.0
                helmets[bid]       = "No Helmet Detected"

        # ── HUD overlay 
        conn    = sqlite3.connect(DB_PATH)
        total_v = conn.execute("SELECT COUNT(*) FROM violations").fetchone()[0]
        conn.close()

        cv2.rectangle(result, (0,0), (430,76), (15,15,15), -1)
        cv2.putText(result,
                    f"Cars:{len(carTracker)}  Bikes:{len(bikeTracker)}  FPS:{fps:.0f}  PPM:{PPM}",
                    (10,22), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (200,200,200), 1)
        cv2.putText(result,
                    f"Limits — Car:{SPEED_LIMIT_CAR}  Bike:{SPEED_LIMIT_BIKE} km/h",
                    (10,44), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (160,160,160), 1)
        cv2.putText(result,
                    f"Violations recorded: {total_v}",
                    (10,66), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (60,120,255), 1)

        cv2.imshow("Traffic Enforcement", result)
        if cv2.waitKey(1) & 0xFF == 27:
            break
        if cv2.getWindowProperty("Traffic Enforcement", cv2.WND_PROP_VISIBLE) < 1:
            break

    video.release()
    cv2.destroyAllWindows()
    print("[INFO] Done.")


if __name__ == '__main__':
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "record.mkv"
    if not os.path.exists(path):
        print(f"[ERROR] Video not found: {path}")
        sys.exit(1)
    trackMultipleObjects(path)
