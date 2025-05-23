import cv2
import time
import threading
import numpy as np
from flask import Flask, jsonify, request
from mediapipe.python.solutions import pose as mp_pose
from mediapipe.python.solutions import hands as mp_hands
from mediapipe.python.solutions import face_mesh as mp_face_mesh

app = Flask(__name__)

# =====================
# Global State
# =====================
timer_running = False
start_time = None
elapsed_time = 0
elapsed_before_stop = 0
drowsy_detected = False
drowsiness_reason = ""

ear_condition_start = None
dy_condition_start = None
hand_condition_start = None
nose_base_y = None

lock = threading.Lock()

# MediaPipe initialization
pose = mp_pose.Pose()
hands = mp_hands.Hands()
face_mesh = mp_face_mesh.FaceMesh()

# =====================
# Timer Functions
# =====================
def timer_loop():
    global start_time, elapsed_time
    while True:
        if timer_running and start_time:
            elapsed_time = time.time() - start_time + elapsed_before_stop
        time.sleep(1)

def format_time(seconds):
    h = int(seconds) // 3600
    m = (int(seconds) % 3600) // 60
    s = int(seconds) % 60
    return f"{h:02}:{m:02}:{s:02}"

@app.route("/start")
def start():
    global timer_running, start_time
    with lock:
        if not timer_running:
            start_time = time.time()
            timer_running = True
            print("[API] Timer started")
    return "Started"

@app.route("/stop")
def stop():
    global timer_running, elapsed_before_stop, start_time
    with lock:
        if timer_running:
            timer_running = False
            elapsed_before_stop = elapsed_time
            start_time = None
            print("[API] Timer stopped")
    return "Stopped"

@app.route("/reset")
def reset():
    global timer_running, start_time, elapsed_time, drowsy_detected
    global ear_condition_start, dy_condition_start, hand_condition_start, drowsiness_reason
    global elapsed_before_stop
    with lock:
        timer_running = False
        start_time = None
        elapsed_time = 0
        elapsed_before_stop = 0
        ear_condition_start = None
        dy_condition_start = None
        hand_condition_start = None
        drowsy_detected = False
        drowsiness_reason = ""
        print("[RESET] System reset")
    return "Reset"

@app.route("/clear_drowsiness")
def clear_drowsiness():
    global drowsy_detected, drowsiness_reason
    global ear_condition_start, dy_condition_start, hand_condition_start
    drowsy_detected = False
    drowsiness_reason = ""
    ear_condition_start = None
    dy_condition_start = None
    hand_condition_start = None
    print("[CLEAR] Drowsiness state cleared")
    return "Cleared"

@app.route("/get_time")
def get_time():
    return jsonify({"time": format_time(elapsed_time)})

@app.route("/update_time", methods=["POST"])
def update_time():
    global elapsed_before_stop
    data = request.get_json()
    h, m, s = map(int, data["time"].split(":"))
    elapsed_before_stop = h * 3600 + m * 60 + s
    print("[RECEIVED TIME]", data["time"])
    return "Time updated"

@app.route("/detect_drowsiness")
def detect_drowsiness():
    return jsonify({"drowsy": drowsy_detected, "reason": drowsiness_reason})

# =====================
# Drowsiness Detection Thread
# =====================
def drowsiness_detection_loop():
    global ear_condition_start, dy_condition_start, hand_condition_start
    global drowsy_detected, drowsiness_reason, nose_base_y

    cap = cv2.VideoCapture(0)
    hand_history = []

    while True:
        ret, frame = cap.read()
        if not ret:
            continue

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        pose_results = pose.process(rgb)
        hands_results = hands.process(rgb)
        face_results = face_mesh.process(rgb)

        current_time = time.time()

        # EAR calculation (only top-bottom distance)
        ear = 0
        if face_results.multi_face_landmarks:
            lm = face_results.multi_face_landmarks[0].landmark
            top = np.array([lm[386].x, lm[386].y])
            bottom = np.array([lm[374].x, lm[374].y])
            ear = np.linalg.norm(top - bottom)  # pure vertical distance
        print(f"[EYE] EAR (top-bottom distance): {ear:.3f}")

        if ear < 0.015:
            if not ear_condition_start:
                ear_condition_start = current_time
            print(f"[EYE] EAR condition duration: {current_time - ear_condition_start:.1f} seconds")
        else:
            ear_condition_start = None

        # DY calculation
        dy = 0
        if pose_results.pose_landmarks:
            nose_y = pose_results.pose_landmarks.landmark[0].y
            if nose_base_y is None:
                nose_base_y = nose_y
            dy = (nose_y - nose_base_y) * 1000
            print(f"[HEAD] DY: {dy:.2f}")
            if dy > 150:
                if not dy_condition_start:
                    dy_condition_start = current_time
                print(f"[HEAD] DY condition duration: {current_time - dy_condition_start:.1f} seconds")
            else:
                dy_condition_start = None

        # Hand movement
        movement = 0
        if hands_results.multi_hand_landmarks:
            lm = hands_results.multi_hand_landmarks[0].landmark[10]
            hand_history.append((lm.x, lm.y, current_time))
            hand_history = [h for h in hand_history if current_time - h[2] <= 5]
            if len(hand_history) >= 2:
                movement = np.mean([
                    np.linalg.norm(np.array(hand_history[i][:2]) - np.array(hand_history[i-1][:2]))
                    for i in range(1, len(hand_history))
                ]) * 1000
        print(f"[HAND] Movement: {movement:.2f}")

        if movement < 13:
            if not hand_condition_start:
                hand_condition_start = current_time
            print(f"[HAND] Movement condition duration: {current_time - hand_condition_start:.1f} seconds")
        else:
            hand_condition_start = None

        if timer_running and not drowsy_detected:
            eye_drowsy = ear_condition_start and current_time - ear_condition_start >= 5
            head_drowsy = dy_condition_start and current_time - dy_condition_start >= 4
            hand_drowsy = hand_condition_start and current_time - hand_condition_start >= 5

            if hand_drowsy and (eye_drowsy or head_drowsy):
                drowsy_detected = True
                drowsiness_reason = "HAND + EYE" if eye_drowsy else "HAND + HEAD"
                print(f"=== DROWSINESS DETECTED: {drowsiness_reason} ===")

        cv2.imshow("Drowsiness Detection", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()

# =====================
# Main Execution
# =====================
if __name__ == "__main__":
    threading.Thread(target=drowsiness_detection_loop, daemon=True).start()
    threading.Thread(target=timer_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=5000)
