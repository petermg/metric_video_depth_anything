import argparse
import cv2
import numpy as np
import os
import sys
import time

import open3d as o3d
import depth_map_tools

np.set_printoptions(suppress=True, precision=4)

if __name__ == '__main__':
    
    # Setup arguments
    parser = argparse.ArgumentParser(description='Take a rgb encoded depth video and a color video, and render them it as a steroscopic 3D video')
    
    parser.add_argument('--depth_video', type=str, help='video file to use as input', required=True)
    parser.add_argument('--color_video', type=str, help='video file to use as color input', required=False)
    parser.add_argument('--xfov', type=int, help='fov in deg in the x-direction, calculated from aspectratio and yfov in not given', required=False)
    parser.add_argument('--yfov', type=int, help='fov in deg in the y-direction, calculated from aspectratio and xfov in not given', required=False)
    parser.add_argument('--max_depth', default=20, type=int, help='the max depth that the video uses', required=False)
    parser.add_argument('--pupillary_distance', default=63, type=int, help='pupillary distance in mm', required=False)
    parser.add_argument('--max_frames', default=-1, type=int, help='quit after max_frames nr of frames', required=False)
    parser.add_argument('--touchly0', action='store_true', help='Render as touchly0 format. ie. stereo video with 3d ', required=False)
    parser.add_argument('--touchly1', action='store_true', help='Render as touchly1 format. ie. mono video with 3d', required=False)
    
    
    args = parser.parse_args()
    
    if args.xfov is None and args.yfov is None:
        print("Either --xfov or --yfov is required.")
        exit(0)
    
    
   
    MODEL_maxOUTPUT_depth = args.max_depth
    
    # Verify input file exists
    if not os.path.isfile(args.depth_video):
        raise Exception("input video does not exist")
    
    color_video = None
    if args.color_video is not None:
        if not os.path.isfile(args.color_video):
            raise Exception("input color_video does not exist")
        color_video = cv2.VideoCapture(args.color_video)
        
    raw_video = cv2.VideoCapture(args.depth_video)
    frame_width, frame_height = int(raw_video.get(cv2.CAP_PROP_FRAME_WIDTH)), int(raw_video.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frame_rate = raw_video.get(cv2.CAP_PROP_FPS)
        
    cam_matrix = depth_map_tools.compute_camera_matrix(args.xfov, args.yfov, frame_width, frame_height)
    out_size = None
    if args.touchly1:
        output_file = args.depth_video + "_Touchly1.mp4"
        out_size = (frame_width, frame_height*2)
    elif args.touchly0:
        output_file = args.depth_video + "_Touchly0.mp4"
        out_size = (frame_width*3, frame_height)
    else:
        output_file = args.depth_video + "_stereo.mp4"
        out_size = (frame_width*2, frame_height)
    
    # avc1 seams to be required for Quest 2 if linux complains use mp4v but those video files wont work on Quest 2
    # Read this to install avc1 codec from source https://swiftlane.com/blog/generating-mp4s-using-opencv-python-with-the-avc1-codec/
    out = cv2.VideoWriter(output_file, cv2.VideoWriter_fourcc(*"avc1"), frame_rate, out_size)
    
    
    left_shift = -(args.pupillary_distance/1000)/2
    right_shift = +(args.pupillary_distance/1000)/2

    frame_n = 0
    while raw_video.isOpened():
        
        print(f"Frame: {frame_n} {frame_n/frame_rate}s")
        frame_n += 1
        ret, raw_frame = raw_video.read()
        if not ret:
            break
        
        rgb = cv2.cvtColor(raw_frame, cv2.COLOR_BGR2RGB)
        
        color_frame = None
        if color_video is not None:
            ret, color_frame = color_video.read()
            color_frame = cv2.cvtColor(color_frame, cv2.COLOR_BGR2RGB)
            
            assert color_frame.shape == rgb.shape, "color image and depth image need to have same width and height" #potential BUG here with mono depth videos
        else:
            color_frame = rgb

        # Decode video depth
        depth = np.zeros((frame_height, frame_width), dtype=np.uint32)
        depth_unit = depth.view(np.uint8).reshape((frame_height, frame_width, 4))
        depth_unit[..., 3] = ((rgb[..., 0].astype(np.uint32) + rgb[..., 1]).astype(np.uint32) / 2)
        depth_unit[..., 2] = rgb[..., 2]
        depth = depth.astype(np.float32)/((255**4)/MODEL_maxOUTPUT_depth)
        
        
        if args.touchly1:
            depth8bit = np.rint(depth*(255/MODEL_maxOUTPUT_depth)).astype(np.uint8)
            touchly_depth = np.repeat(depth8bit[..., np.newaxis], 3, axis=-1)
            touchly_depth = 255 - touchly_depth #Touchly uses reverse depth
            out_image = cv2.vconcat([color_frame, touchly_depth])
        else:
            
            #This is very slow needs optimizing (i think)
            mesh = depth_map_tools.get_mesh_from_depth_map(depth, cam_matrix, color_frame)
            
            #move mesh for left eye render
            mesh.translate([-left_shift, 0.0, 0.0])
            left_image = (depth_map_tools.render(mesh, cam_matrix)*255).astype(np.uint8)
            
            touchly_left_depth = None
            #Touchly1 requires a left eye depthmap
            if args.touchly0:
                left_depth = depth_map_tools.render(mesh, cam_matrix, True)
                left_depth8bit = np.rint(left_depth*(255/MODEL_maxOUTPUT_depth)).astype(np.uint8)
                left_depth8bit[left_depth8bit == 0] = 255 # Any pixel at zero depth needs to move back is is non rederd depth buffer
                left_depth8bit = 255 - left_depth8bit #Touchly uses reverse depth
                touchly_left_depth = np.repeat(left_depth8bit[..., np.newaxis], 3, axis=-1)
            
            #Move mesh back to center
            mesh.translate([left_shift, 0.0, 0.0])
        
            #move mesh for right eye render
            mesh.translate([-right_shift, 0.0, 0.0])
            right_image = (depth_map_tools.render(mesh, cam_matrix)*255).astype(np.uint8)
            
            imgs = [left_image, right_image]
            if touchly_left_depth is not None:
                imgs.append(touchly_left_depth)
            
            out_image = cv2.hconcat(imgs)
        
        
        out.write(cv2.cvtColor(out_image, cv2.COLOR_RGB2BGR))
        
        if args.max_frames < frame_n and args.max_frames != -1:
            break
        
    raw_video.release()
    out.release()

