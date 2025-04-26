import os

def split_filename(string):
    """
    拆分文件路径、文件名和扩展名
    """
    path_to_file = os.path.dirname(string)
    filename, extension = os.path.splitext(os.path.basename(string))
    return path_to_file, filename, extension

def generate_output_name(input_file):
    """
    根据输入文件生成 URDF 输出文件名
    """
    path_to_file, filename, extension = split_filename(input_file)
    if path_to_file == "":
        new_name = filename + ".urdf"
    else:
        new_name = os.path.join(path_to_file, filename + ".urdf")
    return new_name

def write_urdf_text(input_file, output_file):
    """
    写入 URDF 文件内容
    """
    output_name = generate_output_name(input_file)
    _, name, _ = split_filename(input_file)
    print(f"Creating {output_name}...")

    # 可以根据需要参数化这些值
    urdf_content = f"""<?xml version="1.0" ?>
<robot name="{name}">
  <link name="baseLink">
    <inertial>
      <origin rpy="0 0 0" xyz="0 0 0"/>
       <mass value="0.0"/>
       <inertia ixx="0" ixy="0" ixz="0" iyy="0" iyz="0" izz="0"/>
    </inertial>
    <visual>
      <origin rpy="0 0 0" xyz="0 0 0"/>
      <geometry>
        <mesh filename="{name}.obj" scale="1.0 1.0 1.0"/>
      </geometry>
      <material name="white">
       <color rgba="1 1 1 1"/>
     </material>
    </visual>
    <collision>
      <origin rpy="0 0 0" xyz="0 0 0"/>
      <geometry>
        <mesh filename="{name}.obj" scale="1.0 1.0 1.0"/>
      </geometry>
    </collision>
  </link>
</robot>
"""
    with open(output_file, "w") as f:
        f.write(urdf_content)
        print("done")

def convert_obj_to_urdf(input_file, output_file):
    """
    使用新的方式转换 .obj 文件为 .urdf 格式
    """
    # 检查文件是否存在
    if not os.path.exists(input_file):
        raise FileNotFoundError(f"{input_file} does not exist")

    # 写入 URDF 内容
    write_urdf_text(input_file, output_file)

    print(f"URDF file created successfully at {output_file}")
