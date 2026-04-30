from langgraph.graph import END, START, StateGraph
from coin_trading.agents.state import AgentState
from coin_trading.agents.nodes.analyst import technical_analyst_node, sentiment_analyst_node
from coin_trading.agents.nodes.researcher import researcher_debate_node
from coin_trading.agents.nodes.fund_manager import fund_manager_node

def create_trading_agent_graph():
    workflow = StateGraph(AgentState)
    
    workflow.add_node("technical_analyst", technical_analyst_node)
    workflow.add_node("sentiment_analyst", sentiment_analyst_node)
    workflow.add_node("researcher_debate", researcher_debate_node)
    workflow.add_node("fund_manager", fund_manager_node)
    
    workflow.add_edge(START, "technical_analyst")
    workflow.add_edge(START, "sentiment_analyst")
    
    workflow.add_edge("technical_analyst", "researcher_debate")
    workflow.add_edge("sentiment_analyst", "researcher_debate")
    
    workflow.add_edge("researcher_debate", "fund_manager")
    workflow.add_edge("fund_manager", END)
    
    return workflow.compile()
