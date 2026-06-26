### 项目模块化管理

1. 模块化输出。
   - 采用xml标签来解析AI输入，进行标准化格式管理。
   - 根据不同类型的tag在前端设定不同的类来渲染。
2. 模块化提示词
    - 所有Agent的提示词都包含Main/Role/Post三个部分。
    - 所有Agent都必须包含一套系统默认提示词，如：Planner/Coder/Role_Player。
    - 建立一个文件夹集中管理。
    - 默认提示词不可被修改，可被复制后修改。
    - get_prompt参数暂定为 agent_type,preset
    - 提示词组装顺序暂定为：Main-World/Env-Role-User-Data(raw recent history)-Memory-Post(Tone/Style/硬执行约束/输出格式)-Last_User_Input
    - 不同的Agent使用不同的Data和Memory组装方式
3. Memory管理模块化
   - 不同的Agent需要定义不同的记忆写入/读取方式。
4. Tool模块化
    - Tool做成一个库，不同的Agent给不同的默认Tool授权范围。
    - Tool可由User对Agent进行自定义授权。
5. Agent管理员
   - 不同的Agent如何一起协作？采用Manager路由。
   - 该Manager可以是本地死代码，也可以由一个Manager Agent进行动态管理。