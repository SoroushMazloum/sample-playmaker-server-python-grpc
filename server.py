from concurrent import futures
from time import sleep
import service_pb2_grpc as pb2_grpc
import service_pb2 as pb2
from typing import Union
from multiprocessing import Manager, Lock
import logging
import grpc
import argparse


logging.basicConfig(level=logging.DEBUG)

class GrpcAgent:
    def __init__(self, agent_type, uniform_number) -> None:
        self.agent_type: pb2.AgentType = agent_type
        self.uniform_number: int = uniform_number
        self.server_params: Union[pb2.ServerParam, None] = None
        self.player_params: Union[pb2.PlayerParam, None] = None
        self.player_types: dict[int, pb2.PlayerType] = {}
        self.debug_mode: bool = False
    
    def GetAction(self, state: pb2.State):
        if self.agent_type == pb2.AgentType.PlayerT:
            return self.GetPlayerActions(state)
        elif self.agent_type == pb2.AgentType.CoachT:
            return self.GetCoachActions(state)
        elif self.agent_type == pb2.AgentType.TrainerT:
            return self.GetTrainerActions(state)
        
    def GetPlayerActions(self, state: pb2.State):
        actions = []
        if state.world_model.game_mode_type == pb2.GameModeType.PlayOn:
            if state.world_model.self.is_goalie:
                actions.append(pb2.PlayerAction(helios_goalie=pb2.HeliosGoalie()))
            elif state.world_model.self.is_kickable:
                actions.append(pb2.PlayerAction(helios_offensive_planner=pb2.HeliosOffensivePlanner(lead_pass=True,
                                                                                  direct_pass=True,
                                                                                  through_pass=True,
                                                                                  simple_pass=True,
                                                                                  short_dribble=True,
                                                                                  long_dribble=True,
                                                                                  simple_shoot=True,
                                                                                  simple_dribble=True,
                                                                                  cross=True,
                                                                                  server_side_decision=False
                                                                                  )))
                actions.append(pb2.PlayerAction(helios_shoot=pb2.HeliosShoot()))
            else:
                actions.append(pb2.PlayerAction(helios_basic_move=pb2.HeliosBasicMove()))
        else:
            actions.append(pb2.PlayerAction(helios_set_play=pb2.HeliosSetPlay()))
        return pb2.PlayerActions(actions=actions)
    
    def GetCoachActions(self, state: pb2.State):
        actions = []
        actions.append(pb2.CoachAction(do_helios_substitute=pb2.DoHeliosSubstitute()))
        return pb2.CoachActions(actions=actions)
    
    def GetTrainerActions(self, state: pb2.State):
        actions = []
        actions.append(
            pb2.TrainerAction(
                do_move_ball=pb2.DoMoveBall(
                    position=pb2.RpcVector2D(
                        x=0,
                        y=0
                    ),
                    velocity=pb2.RpcVector2D(
                        x=0,
                        y=0
                    ),
                )
            )
        )
        return pb2.TrainerActions(actions=actions)
        
class GameHandler(pb2_grpc.GameServicer):
    def __init__(self, shared_lock, shared_number_of_connections) -> None:
        self.agents: dict[int, GrpcAgent] = {}
        self.shared_lock = shared_lock
        self.shared_number_of_connections = shared_number_of_connections

    def GetPlayerActions(self, state: pb2.State, context):
        logging.debug(f"GetPlayerActions unum {state.register_response.uniform_number} at {state.world_model.cycle}")
        res = self.agents[state.register_response.client_id].GetAction(state)
        logging.debug(f"GetPlayerActions Done unum {res}")
        return res

    def GetCoachActions(self, state: pb2.State, context):
        logging.debug(f"GetCoachActions coach at {state.world_model.cycle}")
        res = self.agents[state.register_response.client_id].GetAction(state)
        return res

    def GetTrainerActions(self, state: pb2.State, context):
        logging.debug(f"GetTrainerActions trainer at {state.world_model.cycle}")
        res = self.agents[state.register_response.client_id].GetAction(state)
        return res

    def SendServerParams(self, serverParams: pb2.ServerParam, context):
        logging.debug(f"Server params received unum {serverParams.register_response.uniform_number}")
        self.agents[serverParams.register_response.client_id].server_params = serverParams
        res = pb2.Empty()
        return res

    def SendPlayerParams(self, playerParams: pb2.PlayerParam, context):
        logging.debug(f"Player params received unum {playerParams.register_response.uniform_number}")
        self.agents[playerParams.register_response.client_id].player_params = playerParams
        res = pb2.Empty()
        return res

    def SendPlayerType(self, playerType: pb2.PlayerType, context):
        logging.debug(f"Player type received unum {playerType.register_response.uniform_number}")
        self.agents[playerType.register_response.client_id].player_types[playerType.id] = playerType
        res = pb2.Empty()
        return res

    def SendInitMessage(self, initMessage: pb2.InitMessage, context):
        logging.debug(f"Init message received unum {initMessage.register_response.uniform_number}")
        self.agents[initMessage.register_response.client_id].debug_mode = initMessage.debug_mode
        res = pb2.Empty()
        return res

    def Register(self, register_request: pb2.RegisterRequest, context):
        logging.debug(f"received register request from team_name: {register_request.team_name} "
                      f"unum: {register_request.uniform_number} "
                      f"agent_type: {register_request.agent_type}")
        with self.shared_lock:
            self.shared_number_of_connections.value += 1
            logging.debug(f"Number of connections {self.shared_number_of_connections.value}")
            team_name = register_request.team_name
            uniform_number = register_request.uniform_number
            agent_type = register_request.agent_type
            self.agents[self.shared_number_of_connections.value] = GrpcAgent(agent_type, uniform_number)
            res = pb2.RegisterResponse(client_id=self.shared_number_of_connections.value,
                                    team_name=team_name,
                                    uniform_number=uniform_number,
                                    agent_type=agent_type)
        return res

    def SendByeCommand(self, register_response: pb2.RegisterResponse, context):
        logging.debug(f"Bye command received unum {register_response.uniform_number}")
        # with shared_lock:
        self.agents.pop(register_response.client_id)
            
        res = pb2.Empty()
        return res
    
    def GetBestPlannerAction(self, pairs: pb2.BestPlannerActionRequest, context):
        logging.debug(f"GetBestPlannerAction cycle:{pairs.state.world_model.cycle} pairs:{len(pairs.pairs)} unum:{pairs.state.register_response.uniform_number}")
        pairs_list: list[int, pb2.RpcActionState] = [(k, v) for k, v in pairs.pairs.items()]
        pairs_list.sort(key=lambda x: x[0])
        best_action = max(pairs_list, key=lambda x: -1000 if x[1].action.parent_index != -1 else x[1].predict_state.ball_position.x)
        logging.debug(f"Best action: {best_action[0]} {best_action[1].action.description} to {best_action[1].action.target_unum} in ({round(best_action[1].action.target_point.x, 2)},{round(best_action[1].action.target_point.y, 2)}) e:{round(best_action[1].evaluation,2)}")
        res = pb2.BestPlannerActionResponse(index=best_action[0])
        return res

def serve(port, shared_lock, shared_number_of_connections):
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=22))
    game_service = GameHandler(shared_lock, shared_number_of_connections)
    pb2_grpc.add_GameServicer_to_server(game_service, server)
    server.add_insecure_port(f'[::]:{port}')
    server.start()
    logging.info(f"Starting server on port {port}")
    
    server.wait_for_termination()
    

def main():
    manager = Manager()
    shared_lock = Lock()  # Create a Lock for synchronization
    shared_number_of_connections = manager.Value('i', 0)
    parser = argparse.ArgumentParser(description='Run play maker server')
    parser.add_argument('-p', '--rpc-port', required=False, help='The port of the server', default=50051)
    args = parser.parse_args()
    serve(args.rpc_port, shared_lock, shared_number_of_connections)
    
if __name__ == '__main__':
    main()
    
