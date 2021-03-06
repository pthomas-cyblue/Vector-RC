#!/usr/bin/env python3

# Copyright (c) 2018 Anki, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License in the file LICENSE.txt or at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Control Vector using a webpage on your computer.

This example lets you control Vector by Remote Control, using a webpage served by Flask.
"""

import io
import json
import sys
import time
import numpy
from lib import flask_helpers

import anki_vector
from anki_vector import util


try:
    from flask import Flask, request, render_template, jsonify
except ImportError:
    sys.exit("Cannot import from flask: Do `pip3 install --user flask` to install")

try:
    from PIL import Image
except ImportError:
    sys.exit("Cannot import from PIL: Do `pip3 install --user Pillow` to install")

def create_default_image(image_width, image_height, do_gradient=False):
    """Create a place-holder PIL image to use until we have a live feed from Vector"""
    image_bytes = bytearray([0x70, 0x70, 0x70]) * image_width * image_height

    if do_gradient:
        i = 0
        for y in range(image_height):
            for x in range(image_width):
                image_bytes[i] = int(255.0 * (x / image_width))   # R
                image_bytes[i + 1] = int(255.0 * (y / image_height))  # G
                image_bytes[i + 2] = 0                                # B
                i += 3

    image = Image.frombytes('RGB', (image_width, image_height), bytes(image_bytes))
    return image


flask_app = Flask(__name__, static_url_path='/static', template_folder='templates')
_default_camera_image = create_default_image(320, 240)
_is_mouse_look_enabled_by_default = False
_is_torch_mode_enabled_by_default = False


def remap_to_range(x, x_min, x_max, out_min, out_max):
    """convert x (in x_min..x_max range) to out_min..out_max range"""
    if x < x_min:
        return out_min
    if x > x_max:
        return out_max
    ratio = (x - x_min) / (x_max - x_min)
    return out_min + ratio * (out_max - out_min)


class RemoteControlVector:

    def __init__(self, robot):
        self.vector = robot
        self.vector.behavior.set_eye_color(hue=0.00, saturation=1.00)
        #self.vector.say_text("AH HA! I AM IN CONTROL NOW")

        name_data_list = self.vector.faces.request_enrolled_names().result()
        print("LIST OF NAMES")
        print(f"{name_data_list}")

        self.drive_forwards = 0
        self.drive_back = 0
        self.turn_left = 0
        self.turn_right = 0
        self.lift_up = 0
        self.lift_down = 0
        self.head_up = 0
        self.head_down = 0
        self.torchIsEnabled = _is_torch_mode_enabled_by_default

        self.go_fast = 0
        self.go_slow = 0

        self.is_mouse_look_enabled = _is_mouse_look_enabled_by_default
        self.torchIsEnabled = _is_torch_mode_enabled_by_default
        self.mouse_dir = 0

        all_anim_names = self.vector.anim.anim_list
        all_anim_names.sort()
        self.anim_names = []

        # Hide a few specific test animations that don't behave well
        bad_anim_names = [
            "ANIMATION_TEST",
            "soundTestAnim"]

        for anim_name in all_anim_names:
            if anim_name not in bad_anim_names:
                self.anim_names.append(anim_name)

        default_anims_for_keys = ["anim_turn_left_01",  # 0
                                  "anim_blackjack_victorwin_01",  # 1
                                  "anim_pounce_success_02",  # 2
                                  "anim_feedback_shutup_01",  # 3
                                  "anim_knowledgegraph_success_01",  # 4
                                  "anim_wakeword_groggyeyes_listenloop_01",  # 5
                                  "anim_fistbump_success_01",  # 6
                                  "anim_reacttoface_unidentified_01",  # 7
                                  "anim_rtpickup_loop_10",  # 8
                                  "anim_volume_stage_05"]  # 9

        self.anim_index_for_key = [0] * 10
        kI = 0
        for default_key in default_anims_for_keys:
            try:
                anim_idx = self.anim_names.index(default_key)
            except ValueError:
                print("Error: default_anim %s is not in the list of animations" % default_key)
                anim_idx = kI
            self.anim_index_for_key[kI] = anim_idx
            kI += 1

        self.action_queue = []
        self.text_to_say = "Hi I'm Vector"

    def set_anim(self, key_index, anim_index):
        self.anim_index_for_key[key_index] = anim_index

    def handle_mouse(self, mouse_x, mouse_y):
        """Called whenever mouse moves
            mouse_x, mouse_y are in in 0..1 range (0,0 = top left, 1,1 = bottom right of window)
        """
        if self.is_mouse_look_enabled:
            mouse_sensitivity = 1.5  # higher = more twitchy
            self.mouse_dir = remap_to_range(mouse_x, 0.0, 1.0, -mouse_sensitivity, mouse_sensitivity)
            self.update_mouse_driving()

            desired_head_angle = remap_to_range(mouse_y, 0.0, 1.0, 45, -25)
            head_angle_delta = desired_head_angle - util.radians(self.vector.head_angle_rad).degrees
            head_vel = head_angle_delta * 0.03
            self.vector.motors.set_head_motor(head_vel)

    def set_mouse_look_enabled(self, is_mouse_look_enabled):
        was_mouse_look_enabled = self.is_mouse_look_enabled
        self.is_mouse_look_enabled = is_mouse_look_enabled
        if not is_mouse_look_enabled:
            # cancel any current mouse-look turning
            self.mouse_dir = 0
            if was_mouse_look_enabled:
                self.update_mouse_driving()
                self.update_head()

    def update_drive_state(self, key_code, is_key_down, speed_changed):
        """Update state of driving intent from keyboard, and if anything changed then call update_driving"""
        update_driving = True
        if key_code == ord('W'):
            self.drive_forwards = is_key_down
        elif key_code == ord('S'):
            self.drive_back = is_key_down
        elif key_code == ord('A'):
            self.turn_left = is_key_down
        elif key_code == ord('D'):
            self.turn_right = is_key_down
        else:
            if not speed_changed:
                update_driving = False
        return update_driving

    def update_lift_state(self, key_code, is_key_down, speed_changed):
        """Update state of lift move intent from keyboard, and if anything changed then call update_lift"""
        update_lift = True
        if key_code == ord('R'):
            self.lift_up = is_key_down
        elif key_code == ord('F'):
            self.lift_down = is_key_down
        else:
            if not speed_changed:
                update_lift = False
        return update_lift

    def update_head_state(self, key_code, is_key_down, speed_changed):
        """Update state of head move intent from keyboard, and if anything changed then call update_head"""
        update_head = True
        if key_code == ord('T'):
            self.head_up = is_key_down
        elif key_code == ord('G'):
            self.head_down = is_key_down
        else:
            if not speed_changed:
                update_head = False
        return update_head    

    def make_vector_dock_with_charger(self, key_code):
        """Make vector dock with his charger when pressing H"""
        if key_code == ord('H'):
            flask_app.remote_control_vector.vector.behavior.drive_on_charger()
        return True

    def handle_key(self, key_code, is_shift_down, is_alt_down, is_key_down):
        """Called on any key press or release
           Holding a key down may result in repeated handle_key calls with is_key_down==True
        """

        # Update desired speed / fidelity of actions based on shift/alt being held
        was_go_fast = self.go_fast
        was_go_slow = self.go_slow

        self.go_fast = is_shift_down
        self.go_slow = is_alt_down

        speed_changed = (was_go_fast != self.go_fast) or (was_go_slow != self.go_slow)

        update_driving = self.update_drive_state(key_code, is_key_down, speed_changed)

        update_lift = self.update_lift_state(key_code, is_key_down, speed_changed)

        update_head = self.update_head_state(key_code, is_key_down, speed_changed)

        make_vector_dock_with_charger = self.make_vector_dock_with_charger(key_code)

        # Update driving, head and lift as appropriate
        if update_driving:
            self.update_mouse_driving()
        if update_head:
            self.update_head()
        if update_lift:
            self.update_lift()
        if make_vector_dock_with_charger:
            self.make_vector_dock_with_charger(key_code)

        # Handle any keys being released (e.g. the end of a key-click)
        if not is_key_down:
            if ord('9') >= key_code >= ord('0'):
                anim_name = self.key_code_to_anim_name(key_code)
                self.queue_action((self.vector.anim.play_animation, anim_name))
            elif key_code == ord(' '):
                self.queue_action((self.vector.say_text, self.text_to_say))

    def key_code_to_anim_name(self, key_code):
        key_num = key_code - ord('0')
        anim_num = self.anim_index_for_key[key_num]
        anim_name = self.anim_names[anim_num]
        return anim_name

    def func_to_name(self, func):
        if func == self.vector.say_text:
            return "say_text"
        if func == self.vector.anim.play_animation:
            return "play_anim"
        return "UNKNOWN"

    def action_to_text(self, action):
        func, args = action
        return self.func_to_name(func) + "( " + str(args) + " )"

    def action_queue_to_text(self, action_queue):
        out_text = ""
        i = 0
        for action in action_queue:
            out_text += "[" + str(i) + "] " + self.action_to_text(action)
            i += 1
        return out_text

    def queue_action(self, new_action):
        if len(self.action_queue) > 10:
            self.action_queue.pop(0)
        self.action_queue.append(new_action)

    def update(self):
        """Try and execute the next queued action"""
        if self.action_queue:
            queued_action, action_args = self.action_queue[0]
            if queued_action(action_args):
                self.action_queue.pop(0)

    def pick_speed(self, fast_speed, mid_speed, slow_speed):
        if self.go_fast:
            if not self.go_slow:
                return fast_speed
        elif self.go_slow:
            return slow_speed
        return mid_speed

    def update_lift(self):
        lift_speed = self.pick_speed(8, 4, 2)
        lift_vel = (self.lift_up - self.lift_down) * lift_speed
        self.vector.motors.set_lift_motor(lift_vel)

    def update_head(self):
        if not self.is_mouse_look_enabled:
            head_speed = self.pick_speed(2, 1, 0.5)
            head_vel = (self.head_up - self.head_down) * head_speed
            self.vector.motors.set_head_motor(head_vel)

    def update_mouse_driving(self):
        drive_dir = (self.drive_forwards - self.drive_back)

        turn_dir = (self.turn_right - self.turn_left) + self.mouse_dir
        if drive_dir < 0:
            # It feels more natural to turn the opposite way when reversing
            turn_dir = -turn_dir

        forward_speed = self.pick_speed(150, 75, 50)
        turn_speed = self.pick_speed(100, 50, 30)

        l_wheel_speed = (drive_dir * forward_speed) + (turn_speed * turn_dir)
        r_wheel_speed = (drive_dir * forward_speed) - (turn_speed * turn_dir)

        self.vector.motors.set_wheel_motors(l_wheel_speed, r_wheel_speed, l_wheel_speed * 4, r_wheel_speed * 4)


def get_anim_sel_drop_down(selectorIndex):
    html_text = """<select onchange="handleDropDownSelect(this)" name="animSelector""" + str(selectorIndex) + """">"""
    i = 0
    for anim_name in flask_app.remote_control_vector.anim_names:
        is_selected_item = (i == flask_app.remote_control_vector.anim_index_for_key[selectorIndex])
        selected_text = ''' selected="selected"''' if is_selected_item else ""
        html_text += """<option value=""" + str(i) + selected_text + """>""" + anim_name + """</option>"""
        i += 1
    html_text += """</select>"""
    return html_text


def get_anim_sel_drop_downs():
    html_text = ""
    for i in range(10):
        # list keys 1..9,0 as that's the layout on the keyboard
        key = i + 1 if (i < 9) else 0
        html_text += str(key) + """: """ + get_anim_sel_drop_down(key) + """<br>"""
    return html_text


def to_js_bool_string(bool_value):
    return "true" if bool_value else "false"


@flask_app.route("/")
def handle_index_page():
    return render_template('index.html')

#def get_audio_stream():
    #audio = flask_app.remote_control_vector.vector.robot.audio.init_audio_feed()

def get_annotated_image():
    # TODO: Update to use annotated image (add annotate module)
    image = flask_app.remote_control_vector.vector.camera.latest_image
    if image is None:
        return _default_camera_image

    return image


def streaming_video():
    """Video streaming generator function"""
    while True:
        if flask_app.remote_control_vector:
            image = get_annotated_image()

            img_io = io.BytesIO()
            image.save(img_io, 'PNG')
            img_io.seek(0)
            yield (b'--frame\r\n'
                   b'Content-Type: image/png\r\n\r\n' + img_io.getvalue() + b'\r\n')
        else:
            time.sleep(.1)


def serve_single_image():
    if flask_app.remote_control_vector:
        image = get_annotated_image()
        if image:
            return flask_helpers.serve_pil_image(image)

    return flask_helpers.serve_pil_image(_default_camera_image)


def is_microsoft_browser(req):
    agent = req.user_agent.string
    return 'Edge/' in agent or 'MSIE ' in agent or 'Trident/' in agent


@flask_app.route("/vectorImage")
def handle_vectorImage():
    if is_microsoft_browser(request):
        return serve_single_image()
    return flask_helpers.stream_video(streaming_video)


def handle_key_event(key_request, is_key_down):
    message = json.loads(key_request.data.decode("utf-8"))
    if flask_app.remote_control_vector:
        flask_app.remote_control_vector.handle_key(key_code=(message['keyCode']), is_shift_down=message['hasShift'],
                                                   is_alt_down=message['hasAlt'], is_key_down=is_key_down)
    return ""


@flask_app.route('/mousemove', methods=['POST'])
def handle_mousemove():
    """Called from Javascript whenever mouse moves"""
    message = json.loads(request.data.decode("utf-8"))
    if flask_app.remote_control_vector:
        flask_app.remote_control_vector.handle_mouse(mouse_x=(message['clientX']), mouse_y=message['clientY'])
    return ""


@flask_app.route('/setMouseLookEnabled', methods=['POST'])
def handle_setMouseLookEnabled():
    """Called from Javascript whenever mouse-look mode is toggled"""
    message = json.loads(request.data.decode("utf-8"))
    if flask_app.remote_control_vector:
        flask_app.remote_control_vector.set_mouse_look_enabled(is_mouse_look_enabled=message['isMouseLookEnabled'])
    return ""


@flask_app.route('/setTorchModeEnabled', methods=['POST'])
def handle_setTorchModeEnabled():
    """Called from Javascript whenever torch mode is toggled"""
    message = json.loads(request.data.decode("utf-8"))    
    flask_app.remote_control_vector.torchIsEnabled = message['isTorchModeEnabled']
    #print("message is ", message)
    setTorchMode()
    return ""   

def setTorchMode():
    #print("Torch is Enabled? ", flask_app.remote_control_vector.torchIsEnabled)
    if flask_app.remote_control_vector:
        if flask_app.remote_control_vector.torchIsEnabled:
            flask_app.remote_control_vector.vector.behavior.set_eye_color(hue=0.00, saturation=0.00)
            flask_app.remote_control_vector.vector.screen.set_screen_to_color(anki_vector.color.Color(rgb=[255, 255, 255]), duration_sec=1.0, interrupt_running=True)
            time.sleep(1)
            setTorchMode()
        else:            
            flask_app.remote_control_vector.vector.behavior.set_eye_color(hue=0.00, saturation=1.00)
            flask_app.remote_control_vector.vector.screen.set_screen_to_color(anki_vector.color.Color(rgb=[0, 0, 0]), duration_sec=0.01, interrupt_running=True)

@flask_app.route('/setFreeplayEnabled', methods=['POST'])
def handle_setFreeplayEnabled():
    """Called from Javascript whenever freeplay mode is toggled on/off"""
    message = json.loads(request.data.decode("utf-8"))
    if flask_app.remote_control_vector:
        isFreeplayEnabled = message['isFreeplayEnabled']
        connection = flask_app.remote_control_vector.vector.conn
        if isFreeplayEnabled == True:
            connection.release_control()
        elif isFreeplayEnabled == False:
            connection.request_control()
            flask_app.remote_control_vector.vector.behavior.set_eye_color(hue=0.00, saturation=1.00)
        
    return ""

@flask_app.route('/setExploreEnabled', methods=['POST'])
def handle_setExploreEnabled():
    """Called from Javascript whenever explore mode is toggled on/off"""
    message = json.loads(request.data.decode("utf-8"))
    if flask_app.remote_control_vector:
        isExploreEnabled = message['isExploreEnabled']
        print(isExploreEnabled)

    return ""

@flask_app.route('/keydown', methods=['POST'])
def handle_keydown():
    """Called from Javascript whenever a key is down (note: can generate repeat calls if held down)"""
    return handle_key_event(request, is_key_down=True)


@flask_app.route('/keyup', methods=['POST'])
def handle_keyup():
    """Called from Javascript whenever a key is released"""
    return handle_key_event(request, is_key_down=False)


@flask_app.route('/dropDownSelect', methods=['POST'])
def handle_dropDownSelect():
    """Called from Javascript whenever an animSelector dropdown menu is selected (i.e. modified)"""
    message = json.loads(request.data.decode("utf-8"))

    item_name_prefix = "animSelector"
    item_name = message['itemName']

    if flask_app.remote_control_vector and item_name.startswith(item_name_prefix):
        item_name_index = int(item_name[len(item_name_prefix):])
        flask_app.remote_control_vector.set_anim(item_name_index, message['selectedIndex'])

    return ""


@flask_app.route('/sayText', methods=['POST'])
def handle_sayText():
    """Called from Javascript whenever the saytext text field is modified"""
    message = json.loads(request.data.decode("utf-8"))
    if flask_app.remote_control_vector:
        flask_app.remote_control_vector.text_to_say = message['textEntered']
    return ""


@flask_app.route('/updateVector', methods=['POST'])
def handle_updateVector():
    if flask_app.remote_control_vector:
        flask_app.remote_control_vector.update()
        action_queue_text = ""
        i = 1
        for action in flask_app.remote_control_vector.action_queue:
            action_queue_text += str(i) + ": " + flask_app.remote_control_vector.action_to_text(action) + "<br>"
            i += 1

        return "Action Queue:<br>" + action_queue_text + "\n"
    return ""
    

faces = {}
@flask_app.route('/updateVectorHud', methods=['GET'])
def handle_updateVectorHud():
    #print('Updating Vectors HUD, Sending Data...')
    for face in flask_app.remote_control_vector.vector.world.visible_faces:
        print("Face name:")
        print(face.name)
        print("Face id:")
        print(face.face_id)
        print("MOUTH")
        print(f"mouth: {face.mouth}")
        print(f"Pose: {face.pose}")
        dictKey = "face"+str(face.face_id)
        print(f"dictKey is: {dictKey}")

        faces[dictKey] = {
            "name": face.name,
            "isVisible": face.is_visible,
            "id": face.face_id,
            "updated id": face.updated_face_id,
            "pose": { "position": { "x": face.pose.position.x, "y": face.pose.position.y, "z": face.pose.position.z}},
            "expression": face.expression,
            "expression_score": json.dumps(list(face.expression_score)),
            "image_rect": { "height": face.last_observed_image_rect.height, "width": face.last_observed_image_rect.width, "x_top_left": face.last_observed_image_rect.x_top_left, "y_top_left": face.last_observed_image_rect.y_top_left}
        }
        
    print(f"faces: {faces}")


    battery_state = flask_app.remote_control_vector.vector.get_battery_state().result()
    if battery_state:
        battery_level = battery_state.battery_level
        battery_charging = battery_state.is_charging
    return jsonify({'leftWheel': flask_app.remote_control_vector.vector.left_wheel_speed_mmps, 'rightWheel': flask_app.remote_control_vector.vector.right_wheel_speed_mmps, 'batteryLevel': battery_level, 'batteryCharging':battery_charging, 'headAngleRad': flask_app.remote_control_vector.vector.head_angle_rad, 'liftHeightmm': flask_app.remote_control_vector.vector.lift_height_mm, 'faces': faces})

@flask_app.route('/updateVectorStats', methods=['GET'])
def handle_updateVectorStats():
    #print('Updating Vectors Stats, Sending Data...')

    return jsonify({'are_motors_moving':flask_app.remote_control_vector.vector.status.are_motors_moving, 'are_wheels_moving':flask_app.remote_control_vector.vector.status.are_wheels_moving, 'is_animating':flask_app.remote_control_vector.vector.status.is_animating, 'is_being_held':flask_app.remote_control_vector.vector.status.is_being_held, 'is_button_pressed':flask_app.remote_control_vector.vector.status.is_button_pressed, 'is_carrying_block':flask_app.remote_control_vector.vector.status.is_carrying_block, 'is_charging':flask_app.remote_control_vector.vector.status.is_charging, 'is_cliff_detected':flask_app.remote_control_vector.vector.status.is_cliff_detected, 'is_docking_to_marker':flask_app.remote_control_vector.vector.status.is_docking_to_marker, 'is_falling':flask_app.remote_control_vector.vector.status.is_falling, 'is_head_in_pos':flask_app.remote_control_vector.vector.status.is_head_in_pos, 'is_in_calm_power_mode':flask_app.remote_control_vector.vector.status.is_in_calm_power_mode, 'is_lift_in_pos':flask_app.remote_control_vector.vector.status.is_lift_in_pos, 'is_on_charger':flask_app.remote_control_vector.vector.status.is_on_charger, 'is_pathing':flask_app.remote_control_vector.vector.status.is_pathing, 'is_picked_up':flask_app.remote_control_vector.vector.status.is_picked_up, 'is_robot_moving':flask_app.remote_control_vector.vector.status.is_robot_moving })

def run():
    args = util.parse_command_args()
    #when audio is read, add this below - ", enable_audio_feed=True"
    with anki_vector.AsyncRobot(args.serial, enable_camera_feed=True) as robot:
        robot.vision.enable_display_camera_feed_on_face()
        robot.vision.enable_face_detection(detect_faces=True, estimate_expression=True)
        flask_app.remote_control_vector = RemoteControlVector(robot)

        #robot.behavior.drive_off_charger()

        flask_helpers.run_flask(flask_app)

if __name__ == '__main__':
    try:
        run()
    except KeyboardInterrupt as e:
        pass
    except anki_vector.exceptions.VectorConnectionException as e:
        sys.exit("A connection error occurred: %s" % e)
